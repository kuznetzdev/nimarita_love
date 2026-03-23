from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

from aiohttp import web

from nimarita.catalog import care_recipient_hint, care_reply_tone_label, care_tone_label, get_quick_reply_pages
from nimarita.config import Settings
from nimarita.domain.enums import CareDispatchStatus, RelationshipRole, ReminderIntervalUnit, ReminderRuleKind
from nimarita.domain.errors import AccessDeniedError, ConflictError, NotFoundError, ValidationError
from nimarita.domain.models import DashboardState
from nimarita.logging import reset_request_id, set_request_id
from nimarita.services.audit import AuditService
from nimarita.services.care import CareService
from nimarita.services.pairing import InviteIssueResult, PairingService
from nimarita.services.reminders import ReminderService, reminder_kind_label
from nimarita.services.system import SystemService
from nimarita.services.users import UserService
from nimarita.telegram.notifier import TelegramNotifier
from nimarita.web.auth import SessionManager, TelegramInitDataVerifier, WebAuthError

logger = logging.getLogger(__name__)

FRONTEND_PATH = Path(__file__).resolve().parent / 'static' / 'index.html'
NO_STORE_HEADERS = {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
}
API_CORS_ALLOW_METHODS = 'GET, POST, OPTIONS'
API_CORS_ALLOW_HEADERS = 'Authorization, Content-Type'
API_CORS_MAX_AGE_SECONDS = '86400'
MAX_API_BODY_BYTES = 64 * 1024
_API_BASE_PLACEHOLDER = 'PLACEHOLDER_API_BASE'
AUDIT_SERVICE_APP_KEY: Final[web.AppKey[AuditService]] = web.AppKey('audit_service', AuditService)
ALLOWED_CORS_ORIGINS_APP_KEY: Final[web.AppKey[tuple[str, ...]]] = web.AppKey('allowed_cors_origins', tuple[str, ...])


def _build_cors_headers(request: web.Request) -> dict[str, str]:
    origin = request.headers.get('Origin')
    if not origin:
        return {}
    allowed = request.app.get(ALLOWED_CORS_ORIGINS_APP_KEY, ())
    if origin not in allowed:
        return {}
    return {
        'Access-Control-Allow-Origin': origin,
        'Access-Control-Allow-Methods': API_CORS_ALLOW_METHODS,
        'Access-Control-Allow-Headers': API_CORS_ALLOW_HEADERS,
        'Access-Control-Max-Age': API_CORS_MAX_AGE_SECONDS,
        'Vary': 'Origin',
    }


@web.middleware
async def request_context_middleware(request: web.Request, handler: web.Handler) -> web.StreamResponse:
    request_id = uuid.uuid4().hex[:16]
    token = set_request_id(request_id)
    request['request_id'] = request_id
    try:
        response = await handler(request)
    finally:
        reset_request_id(token)
    response.headers['X-Request-ID'] = request_id
    return response


@web.middleware
async def access_log_middleware(request: web.Request, handler: web.Handler) -> web.StreamResponse:
    started = time.perf_counter()
    response = await handler(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        'HTTP %s %s -> %s %.1fms origin=%s request_id=%s',
        request.method,
        request.path,
        response.status,
        elapsed_ms,
        request.headers.get('Origin'),
        request.get('request_id'),
    )
    return response


@web.middleware
async def cors_middleware(request: web.Request, handler: web.Handler) -> web.StreamResponse:
    if request.path.startswith('/api/') and request.method == 'OPTIONS':
        response = web.Response(status=204, headers=NO_STORE_HEADERS)
    else:
        response = await handler(request)
    for key, value in _build_cors_headers(request).items():
        response.headers[key] = value
    return response


@web.middleware
async def error_middleware(request: web.Request, handler: web.Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except WebAuthError as error:
        audit = request.app.get(AUDIT_SERVICE_APP_KEY)
        if audit is not None:
            await audit.record(
                action='web_auth_failed',
                entity_type='auth',
                entity_id=request.path,
                payload={'error': error.message, 'method': request.method},
            )
        return web.json_response({'ok': False, 'error': error.message}, status=error.status, headers=NO_STORE_HEADERS)
    except AccessDeniedError as error:
        audit = request.app.get(AUDIT_SERVICE_APP_KEY)
        if audit is not None:
            await audit.record(
                action='web_access_denied',
                entity_type='auth',
                entity_id=request.path,
                payload={'error': str(error), 'method': request.method},
            )
        return web.json_response({'ok': False, 'error': str(error)}, status=403, headers=NO_STORE_HEADERS)
    except ValidationError as error:
        return web.json_response({'ok': False, 'error': str(error)}, status=400, headers=NO_STORE_HEADERS)
    except ConflictError as error:
        return web.json_response({'ok': False, 'error': str(error)}, status=409, headers=NO_STORE_HEADERS)
    except NotFoundError as error:
        return web.json_response({'ok': False, 'error': str(error)}, status=404, headers=NO_STORE_HEADERS)
    except Exception:
        logger.exception('Unhandled web error for %s %s', request.method, request.path)
        return web.json_response(
            {'ok': False, 'error': 'Внутренняя ошибка сервера.'},
            status=500,
            headers=NO_STORE_HEADERS,
        )


class WebServer:
    def __init__(
        self,
        *,
        settings: Settings,
        user_service: UserService,
        pairing_service: PairingService,
        reminder_service: ReminderService,
        care_service: CareService,
        notifier: TelegramNotifier,
        audit: AuditService,
        system: SystemService,
        frontend_path: Path = FRONTEND_PATH,
    ) -> None:
        self._settings = settings
        self._user_service = user_service
        self._pairing_service = pairing_service
        self._reminder_service = reminder_service
        self._care_service = care_service
        self._notifier = notifier
        self._audit = audit
        self._system = system
        self._frontend_path = frontend_path
        self._verifier = TelegramInitDataVerifier(
            bot_token=settings.bot_token,
            ttl_seconds=settings.init_data_ttl_seconds,
        )
        self._sessions = SessionManager(
            secret=settings.session_secret,
            ttl_seconds=settings.session_ttl_seconds,
        )
        self._frontend_html: str | None = None
        self._app = self._build_app()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def _build_frontend_api_base(self) -> str:
        public_url = self._settings.webapp_public_url or ''
        if not public_url:
            return ''
        parsed = urlparse(public_url)
        if parsed.scheme and parsed.netloc:
            return f'{parsed.scheme}://{parsed.netloc}'
        return public_url.rstrip('/')

    def _get_frontend_html(self) -> str | None:
        """Read and cache index.html with PLACEHOLDER_API_BASE substituted."""
        if self._frontend_html is not None:
            return self._frontend_html
        if not self._frontend_path.exists():
            return None
        raw = self._frontend_path.read_text(encoding='utf-8')
        api_base = self._build_frontend_api_base()
        self._frontend_html = raw.replace(_API_BASE_PLACEHOLDER, api_base)
        logger.info('Frontend loaded: substituted API_BASE=%r', api_base)
        return self._frontend_html

    async def start(self) -> None:
        if not self._settings.webapp_enabled or self._runner is not None:
            return
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._settings.webapp_host, port=self._settings.webapp_port)
        await self._site.start()
        logger.info('Web server started on http://%s:%s', self._settings.webapp_host, self._settings.webapp_port)

    async def stop(self) -> None:
        if self._runner is None:
            return
        await self._runner.cleanup()
        self._runner = None
        self._site = None

    def _build_app(self) -> web.Application:
        app = web.Application(
            client_max_size=MAX_API_BODY_BYTES,
            middlewares=[request_context_middleware, access_log_middleware, cors_middleware, error_middleware],
        )
        app[AUDIT_SERVICE_APP_KEY] = self._audit
        app[ALLOWED_CORS_ORIGINS_APP_KEY] = self._settings.allowed_cors_origins
        app.router.add_get('/', self._index)
        app.router.add_get('/health', self._health_live)
        app.router.add_get('/health/live', self._health_live)
        app.router.add_get('/health/ready', self._health_ready)
        app.router.add_get('/api/v1/health', self._health_live)
        app.router.add_get('/api/v1/health/live', self._health_live)
        app.router.add_get('/api/v1/health/ready', self._health_ready)
        app.router.add_post('/api/v1/auth', self._auth)
        app.router.add_post('/api/v1/profile', self._update_profile)
        app.router.add_get('/api/v1/state', self._state)
        app.router.add_post('/api/v1/pairs/invite', self._create_invite)
        app.router.add_post('/api/v1/pairs/invite/cancel', self._cancel_invite)
        app.router.add_post('/api/v1/pairs/accept', self._accept_invite)
        app.router.add_post('/api/v1/pairs/reject', self._reject_invite)
        app.router.add_post('/api/v1/pairs/unpair', self._unpair)
        app.router.add_get('/api/v1/reminders', self._list_reminders)
        app.router.add_post('/api/v1/reminders', self._create_reminder)
        app.router.add_post('/api/v1/reminders/{rule_id}', self._update_reminder)
        app.router.add_post('/api/v1/reminders/{rule_id}/cancel', self._cancel_reminder)
        app.router.add_get('/api/v1/care/templates', self._list_care_templates)
        app.router.add_get('/api/v1/care/history', self._list_care_history)
        app.router.add_post('/api/v1/care/send', self._send_care)
        app.router.add_post('/api/v1/care/respond', self._respond_care)
        app.router.add_post('/api/v1/care/send-custom', self._send_custom_care)
        app.router.add_post('/api/v1/care/respond-custom', self._respond_custom_care)
        return app

    async def _index(self, request: web.Request) -> web.Response:
        del request
        html = self._get_frontend_html()
        if html is None:
            return web.Response(
                text='Фронтенд мини-приложения не найден.',
                content_type='text/plain',
                headers=NO_STORE_HEADERS,
            )
        return web.Response(
            text=html,
            content_type='text/html',
            charset='utf-8',
            headers=NO_STORE_HEADERS,
        )

    async def _health_live(self, request: web.Request) -> web.Response:
        del request
        payload = {
            'ok': True,
            'service': 'nimarita',
            'started_at': self._system.started_at.isoformat(),
        }
        return web.json_response(payload, headers=NO_STORE_HEADERS)

    async def _health_ready(self, request: web.Request) -> web.Response:
        del request
        payload = await self._system.build_readiness_payload()
        status = 200 if payload['ok'] else 503
        return web.json_response(payload, status=status, headers=NO_STORE_HEADERS)

    async def _auth(self, request: web.Request) -> web.Response:
        body = await self._read_json(request)
        init_data = str(body.get('init_data') or '')
        start_param = _read_optional_text(body.get('start_param'))
        snapshot = self._verifier.verify(init_data)
        user = await self._user_service.touch_web_user(snapshot)
        await self._audit.record(
            action='web_auth_success',
            entity_type='auth',
            entity_id='session',
            actor_user_id=user.id,
            payload={'telegram_user_id': user.telegram_user_id},
        )
        invite_preview = None
        invite_preview_error: str | None = None
        if start_param and start_param.startswith('invite_'):
            raw_token = start_param.removeprefix('invite_')
            try:
                preview = await self._pairing_service.preview_invite(user.telegram_user_id, raw_token)
            except (ConflictError, NotFoundError, ValidationError) as error:
                invite_preview_error = str(error)
            else:
                invite_preview = {
                    'invite_id': preview.invite.id,
                    'inviter': self._serialize_user(preview.inviter),
                    'expires_at': preview.invite.expires_at.isoformat(),
                }
        dashboard_payload = await self._build_dashboard_payload(user.telegram_user_id)
        session_token = self._sessions.issue(telegram_user_id=user.telegram_user_id)
        return web.json_response(
            {
                'ok': True,
                'session_token': session_token,
                'user': self._serialize_user(user),
                **dashboard_payload,
                'start_param': start_param,
                'invite_preview': invite_preview,
                'invite_preview_error': invite_preview_error,
            },
            headers=NO_STORE_HEADERS,
        )

    async def _state(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        dashboard_payload = await self._build_dashboard_payload(telegram_user_id)
        return web.json_response({'ok': True, **dashboard_payload}, headers=NO_STORE_HEADERS)

    async def _update_profile(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        body = await self._read_json(request)
        role_text = (_read_optional_text(body.get('relationship_role')) or RelationshipRole.UNSPECIFIED.value).lower()
        try:
            role = RelationshipRole(role_text)
        except ValueError as error:
            raise WebAuthError(status=400, message='Некорректная роль. Используй woman, man или unspecified.') from error
        user = await self._user_service.set_relationship_role(telegram_user_id, role)
        dashboard_payload = await self._build_dashboard_payload(telegram_user_id)
        return web.json_response({'ok': True, 'user': self._serialize_user(user), **dashboard_payload}, headers=NO_STORE_HEADERS)

    async def _create_invite(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        result = await self._pairing_service.create_invite(telegram_user_id)
        dashboard_payload = await self._build_dashboard_payload(telegram_user_id)
        return web.json_response(
            {'ok': True, 'invite': self._serialize_invite_result(result), **dashboard_payload},
            status=201,
            headers=NO_STORE_HEADERS,
        )

    async def _cancel_invite(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        await self._pairing_service.cancel_outgoing_invite(telegram_user_id)
        dashboard_payload = await self._build_dashboard_payload(telegram_user_id)
        return web.json_response({'ok': True, **dashboard_payload}, headers=NO_STORE_HEADERS)

    async def _accept_invite(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        body = await self._read_json(request)
        raw_token = _read_optional_text(body.get('token'))
        invite_id = _read_optional_int(body.get('invite_id'))
        if raw_token:
            _pair, inviter, invitee = await self._pairing_service.accept_invite_by_token(telegram_user_id, raw_token)
        elif invite_id is not None:
            _pair, inviter, invitee = await self._pairing_service.accept_invite_by_id(telegram_user_id, invite_id)
        else:
            raise WebAuthError(status=400, message='Нужен token приглашения или invite_id.')
        await self._notifier.notify_pair_confirmed(inviter, invitee)
        dashboard_payload = await self._build_dashboard_payload(telegram_user_id)
        return web.json_response({'ok': True, **dashboard_payload}, headers=NO_STORE_HEADERS)

    async def _reject_invite(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        body = await self._read_json(request)
        raw_token = _read_optional_text(body.get('token'))
        invite_id = _read_optional_int(body.get('invite_id'))
        if raw_token:
            _invite, inviter, rejector = await self._pairing_service.reject_invite_by_token(telegram_user_id, raw_token)
        elif invite_id is not None:
            _invite, inviter, rejector = await self._pairing_service.reject_invite_by_id(telegram_user_id, invite_id)
        else:
            raise WebAuthError(status=400, message='Нужен token приглашения или invite_id.')
        await self._notifier.notify_pair_rejected(inviter, rejector)
        dashboard_payload = await self._build_dashboard_payload(telegram_user_id)
        return web.json_response({'ok': True, **dashboard_payload}, headers=NO_STORE_HEADERS)

    async def _unpair(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        _pair, actor, partner = await self._pairing_service.unpair(telegram_user_id)
        await self._notifier.notify_pair_closed(actor, partner)
        dashboard_payload = await self._build_dashboard_payload(telegram_user_id)
        return web.json_response({'ok': True, **dashboard_payload}, headers=NO_STORE_HEADERS)

    async def _list_reminders(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        reminders = [
            self._serialize_reminder(item)
            for item in await self._reminder_service.list_pair_reminders(telegram_user_id=telegram_user_id)
        ]
        return web.json_response({'ok': True, 'reminders': reminders}, headers=NO_STORE_HEADERS)

    async def _create_reminder(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        body = await self._read_json(request)
        text = _read_optional_text(body.get('text')) or ''
        scheduled_for_local = _read_optional_text(body.get('scheduled_for_local')) or ''
        timezone = _read_optional_text(body.get('timezone')) or self._settings.default_timezone
        kind = _read_reminder_kind(body.get('kind'))
        recurrence_every = _read_recurrence_every(body.get('recurrence_every'))
        recurrence_unit = _read_recurrence_unit(body.get('recurrence_unit'))
        envelope = await self._reminder_service.create_reminder(
            telegram_user_id=telegram_user_id,
            text=text,
            scheduled_for_local=scheduled_for_local,
            timezone=timezone,
            kind=kind,
            recurrence_every=recurrence_every,
            recurrence_unit=recurrence_unit,
        )
        reminders = [
            self._serialize_reminder(item)
            for item in await self._reminder_service.list_pair_reminders(telegram_user_id=telegram_user_id)
        ]
        return web.json_response(
            {'ok': True, 'reminder': self._serialize_reminder(envelope), 'reminders': reminders},
            status=201,
            headers=NO_STORE_HEADERS,
        )

    async def _update_reminder(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        rule_id_text = request.match_info.get('rule_id', '')
        try:
            rule_id = int(rule_id_text)
        except ValueError as error:
            raise WebAuthError(status=400, message='Некорректный идентификатор напоминания.') from error
        body = await self._read_json(request)
        text = _read_optional_text(body.get('text')) or ''
        scheduled_for_local = _read_optional_text(body.get('scheduled_for_local')) or ''
        timezone = _read_optional_text(body.get('timezone')) or self._settings.default_timezone
        kind = _read_reminder_kind(body.get('kind'))
        recurrence_every = _read_recurrence_every(body.get('recurrence_every'))
        recurrence_unit = _read_recurrence_unit(body.get('recurrence_unit'))
        envelope = await self._reminder_service.update_reminder(
            telegram_user_id=telegram_user_id,
            rule_id=rule_id,
            text=text,
            scheduled_for_local=scheduled_for_local,
            timezone=timezone,
            kind=kind,
            recurrence_every=recurrence_every,
            recurrence_unit=recurrence_unit,
        )
        reminders = [
            self._serialize_reminder(item)
            for item in await self._reminder_service.list_pair_reminders(telegram_user_id=telegram_user_id)
        ]
        return web.json_response(
            {'ok': True, 'reminder': self._serialize_reminder(envelope), 'reminders': reminders},
            headers=NO_STORE_HEADERS,
        )

    async def _cancel_reminder(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        rule_id_text = request.match_info.get('rule_id', '')
        try:
            rule_id = int(rule_id_text)
        except ValueError as error:
            raise WebAuthError(status=400, message='Некорректный идентификатор правила напоминания.') from error
        envelope = await self._reminder_service.cancel_reminder(telegram_user_id=telegram_user_id, rule_id=rule_id)
        reminders = [
            self._serialize_reminder(item)
            for item in await self._reminder_service.list_pair_reminders(telegram_user_id=telegram_user_id)
        ]
        return web.json_response(
            {'ok': True, 'reminder': self._serialize_reminder(envelope), 'reminders': reminders},
            headers=NO_STORE_HEADERS,
        )

    async def _list_care_templates(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        category = _read_optional_text(request.query.get('category'))
        templates = [
            self._serialize_care_template(item)
            for item in await self._care_service.list_templates(telegram_user_id=telegram_user_id, category=category)
        ]
        return web.json_response({'ok': True, 'templates': templates}, headers=NO_STORE_HEADERS)

    async def _list_care_history(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        history = [
            self._serialize_care_dispatch(item, viewer_telegram_user_id=telegram_user_id)
            for item in await self._care_service.list_history(
                telegram_user_id=telegram_user_id,
                limit=self._settings.care_history_limit,
            )
        ]
        return web.json_response({'ok': True, 'history': history}, headers=NO_STORE_HEADERS)

    async def _send_care(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        body = await self._read_json(request)
        template_code = _read_optional_text(body.get('template_code'))
        if not template_code:
            raise WebAuthError(status=400, message='Нужно указать template_code.')
        envelope = await self._care_service.queue_template(
            telegram_user_id=telegram_user_id,
            template_code=template_code,
        )
        history = [
            self._serialize_care_dispatch(item, viewer_telegram_user_id=telegram_user_id)
            for item in await self._care_service.list_history(
                telegram_user_id=telegram_user_id,
                limit=self._settings.care_history_limit,
            )
        ]
        return web.json_response(
            {
                'ok': True,
                'dispatch': self._serialize_care_dispatch(envelope, viewer_telegram_user_id=telegram_user_id),
                'history': history,
            },
            status=202,
            headers=NO_STORE_HEADERS,
        )

    async def _respond_care(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        body = await self._read_json(request)
        dispatch_id = _read_optional_int(body.get('dispatch_id'))
        reply_code = _read_optional_text(body.get('reply_code'))
        if dispatch_id is None:
            raise WebAuthError(status=400, message='Нужен dispatch_id для ответа.')
        if not reply_code:
            raise WebAuthError(status=400, message='Нужен reply_code для ответа.')
        result = await self._care_service.register_quick_reply(
            telegram_user_id=telegram_user_id,
            dispatch_id=dispatch_id,
            reply_code=reply_code,
        )
        await self._notifier.notify_care_response(result)
        history = [
            self._serialize_care_dispatch(item, viewer_telegram_user_id=telegram_user_id)
            for item in await self._care_service.list_history(
                telegram_user_id=telegram_user_id,
                limit=self._settings.care_history_limit,
            )
        ]
        return web.json_response(
            {
                'ok': True,
                'dispatch': self._serialize_care_dispatch(result.envelope, viewer_telegram_user_id=telegram_user_id),
                'history': history,
            },
            headers=NO_STORE_HEADERS,
        )

    async def _send_custom_care(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        body = await self._read_json(request)
        title = _read_optional_text(body.get('title')) or 'Моё сообщение'
        message = _read_optional_text(body.get('message')) or ''
        emoji = _read_optional_text(body.get('emoji')) or '💌'
        envelope = await self._care_service.queue_custom(
            telegram_user_id=telegram_user_id,
            title=title,
            body=message,
            emoji=emoji,
        )
        history = [
            self._serialize_care_dispatch(item, viewer_telegram_user_id=telegram_user_id)
            for item in await self._care_service.list_history(
                telegram_user_id=telegram_user_id,
                limit=self._settings.care_history_limit,
            )
        ]
        return web.json_response(
            {
                'ok': True,
                'dispatch': self._serialize_care_dispatch(envelope, viewer_telegram_user_id=telegram_user_id),
                'history': history,
            },
            status=202,
            headers=NO_STORE_HEADERS,
        )

    async def _respond_custom_care(self, request: web.Request) -> web.Response:
        telegram_user_id = await self._require_session(request)
        body = await self._read_json(request)
        dispatch_id = _read_optional_int(body.get('dispatch_id'))
        if dispatch_id is None:
            raise WebAuthError(status=400, message='Нужен dispatch_id для ответа.')
        title = _read_optional_text(body.get('title')) or 'Мой ответ'
        message = _read_optional_text(body.get('message')) or ''
        emoji = _read_optional_text(body.get('emoji')) or '💗'
        result = await self._care_service.register_custom_reply(
            telegram_user_id=telegram_user_id,
            dispatch_id=dispatch_id,
            title=title,
            body=message,
            emoji=emoji,
        )
        await self._notifier.notify_care_response(result)
        history = [
            self._serialize_care_dispatch(item, viewer_telegram_user_id=telegram_user_id)
            for item in await self._care_service.list_history(
                telegram_user_id=telegram_user_id,
                limit=self._settings.care_history_limit,
            )
        ]
        return web.json_response(
            {
                'ok': True,
                'dispatch': self._serialize_care_dispatch(result.envelope, viewer_telegram_user_id=telegram_user_id),
                'history': history,
            },
            headers=NO_STORE_HEADERS,
        )

    async def _build_dashboard_payload(self, telegram_user_id: int) -> dict[str, object]:
        state = await self._pairing_service.get_dashboard(telegram_user_id)
        reminders: list[dict[str, object]] = []
        care_templates: list[dict[str, object]] = []
        care_history: list[dict[str, object]] = []
        if state.active_pair is not None:
            reminders = [
                self._serialize_reminder(item)
                for item in await self._reminder_service.list_pair_reminders(telegram_user_id=telegram_user_id)
            ]
            care_templates = [
                self._serialize_care_template(item)
                for item in await self._care_service.list_templates(telegram_user_id=telegram_user_id)
            ]
            care_history = [
                self._serialize_care_dispatch(item, viewer_telegram_user_id=telegram_user_id)
                for item in await self._care_service.list_history(
                    telegram_user_id=telegram_user_id,
                    limit=self._settings.care_history_limit,
                )
            ]
        return {
            'state': self._serialize_state(state),
            'reminders': reminders,
            'care_templates': care_templates,
            'care_history': care_history,
        }

    async def _read_json(self, request: web.Request) -> dict[str, object]:
        try:
            body = await request.json()
        except Exception as error:
            raise WebAuthError(status=400, message='Некорректный JSON в теле запроса.') from error
        if not isinstance(body, dict):
            raise WebAuthError(status=400, message='JSON в теле запроса должен быть объектом.')
        return body

    async def _require_session(self, request: web.Request) -> int:
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            raise WebAuthError(status=401, message='Отсутствует Bearer-токен сессии.')
        token = header.removeprefix('Bearer ').strip()
        telegram_user_id = self._sessions.verify(token)
        if not await self._user_service.is_allowed(telegram_user_id):
            raise AccessDeniedError('Доступ к web-сессии ограничен.')
        user = await self._user_service.get_by_telegram_user_id(telegram_user_id)
        if user is None:
            raise WebAuthError(status=401, message='Сессия пользователя больше недействительна. Войди заново.')
        return telegram_user_id

    def _serialize_user(self, user: Any) -> dict[str, Any]:
        return {
            'id': user.id,
            'telegram_user_id': user.telegram_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'display_name': user.display_name,
            'timezone': user.timezone,
            'relationship_role': user.relationship_role.value,
            'relationship_role_label': user.relationship_role_label,
            'private_chat_id': user.private_chat_id,
            'started_bot': user.started_bot,
        }

    def _serialize_state(self, state: DashboardState) -> dict[str, Any]:
        return {
            'mode': state.mode,
            'user': self._serialize_user(state.user),
            'partner': self._serialize_user(state.partner) if state.partner is not None else None,
            'active_pair': {
                'id': state.active_pair.id,
                'status': state.active_pair.status.value,
                'confirmed_at': state.active_pair.confirmed_at.isoformat() if state.active_pair.confirmed_at else None,
            }
            if state.active_pair is not None
            else None,
            'outgoing_invite': {
                'id': state.outgoing_invite.id,
                'expires_at': state.outgoing_invite.expires_at.isoformat(),
                'status': state.outgoing_invite.status.value,
            }
            if state.outgoing_invite is not None
            else None,
            'incoming_invite': {
                'id': state.incoming_invite.id,
                'expires_at': state.incoming_invite.expires_at.isoformat(),
                'status': state.incoming_invite.status.value,
                'inviter': self._serialize_user(state.incoming_inviter) if state.incoming_inviter is not None else None,
            }
            if state.incoming_invite is not None
            else None,
        }

    def _serialize_invite_result(self, result: InviteIssueResult) -> dict[str, Any]:
        return {
            'id': result.invite.id,
            'expires_at': result.invite.expires_at.isoformat(),
            'bot_start_link': result.links.bot_start_link,
            'mini_app_link': result.links.mini_app_link,
            'token': result.raw_token,
        }

    def _serialize_reminder(self, envelope) -> dict[str, Any]:
        return {
            'rule_id': envelope.rule.id,
            'occurrence_id': envelope.occurrence.id,
            'kind': envelope.rule.kind.value,
            'kind_label': reminder_kind_label(
                envelope.rule.kind,
                recurrence_every=envelope.rule.recurrence_every,
                recurrence_unit=envelope.rule.recurrence_unit,
            ),
            'text': envelope.occurrence.text,
            'status': envelope.occurrence.status.value,
            'rule_status': envelope.rule.status.value,
            'handled_action': envelope.occurrence.handled_action,
            'scheduled_at_utc': envelope.occurrence.scheduled_at_utc.isoformat(),
            'next_attempt_at_utc': envelope.occurrence.next_attempt_at_utc.isoformat(),
            'origin_scheduled_at_utc': envelope.rule.origin_scheduled_at_utc.isoformat(),
            'creator_timezone': envelope.rule.creator_timezone,
            'recurrence_every': envelope.rule.recurrence_every,
            'recurrence_unit': envelope.rule.recurrence_unit.value if envelope.rule.recurrence_unit else None,
            'cancelled_at': envelope.rule.cancelled_at.isoformat() if envelope.rule.cancelled_at else None,
            'creator': self._serialize_user(envelope.creator),
            'recipient': self._serialize_user(envelope.recipient),
            'delivery_attempts_count': envelope.occurrence.delivery_attempts_count,
            'last_error': envelope.occurrence.last_error,
            'telegram_message_id': envelope.occurrence.telegram_message_id,
        }

    def _serialize_care_template(self, template) -> dict[str, Any]:
        return {
            'id': template.id,
            'template_code': template.template_code,
            'category': template.category,
            'category_label': template.category_label,
            'title': template.title,
            'body': template.body,
            'emoji': template.emoji,
            'sender_role': template.sender_role.value,
            'recipient_role': template.recipient_role.value,
            'recipient_hint': care_recipient_hint(template.recipient_role),
            'tone_label': care_tone_label(template.category),
            'sort_order': template.sort_order,
        }

    def _serialize_quick_reply(self, reply) -> dict[str, object]:
        return {
            'code': reply.code,
            'category': reply.category,
            'title': reply.title,
            'body': reply.body,
            'emoji': reply.emoji,
            'tone_label': care_reply_tone_label(reply.category),
            'sort_order': reply.sort_order,
        }

    def _serialize_care_dispatch(self, envelope, *, viewer_telegram_user_id: int) -> dict[str, Any]:
        is_inbound = envelope.sender.telegram_user_id != viewer_telegram_user_id
        quick_replies: list[dict[str, object]] = []
        if is_inbound and envelope.dispatch.status == CareDispatchStatus.SENT:
            quick_replies = [
                self._serialize_quick_reply(reply)
                for page in get_quick_reply_pages(envelope.dispatch.category)[:2]
                for reply in page
            ]
        return {
            'id': envelope.dispatch.id,
            'pair_id': envelope.dispatch.pair_id,
            'template_code': envelope.dispatch.template_code,
            'category': envelope.dispatch.category,
            'category_label': envelope.dispatch.category_label,
            'title': envelope.dispatch.title,
            'body': envelope.dispatch.body,
            'emoji': envelope.dispatch.emoji,
            'recipient_hint': care_recipient_hint(envelope.recipient.relationship_role),
            'tone_label': care_tone_label(envelope.dispatch.category),
            'status': envelope.dispatch.status.value,
            'telegram_message_id': envelope.dispatch.telegram_message_id,
            'response_code': envelope.dispatch.response_code,
            'response_title': envelope.dispatch.response_title,
            'response_body': envelope.dispatch.response_body,
            'response_emoji': envelope.dispatch.response_emoji,
            'response_clicked_at': envelope.dispatch.response_clicked_at.isoformat() if envelope.dispatch.response_clicked_at else None,
            'next_attempt_at_utc': envelope.dispatch.next_attempt_at_utc.isoformat() if envelope.dispatch.next_attempt_at_utc else None,
            'processing_started_at': envelope.dispatch.processing_started_at.isoformat() if envelope.dispatch.processing_started_at else None,
            'delivery_attempts_count': envelope.dispatch.delivery_attempts_count,
            'sent_at': envelope.dispatch.sent_at.isoformat() if envelope.dispatch.sent_at else None,
            'delivered_at': envelope.dispatch.delivered_at.isoformat() if envelope.dispatch.delivered_at else None,
            'last_error': envelope.dispatch.last_error,
            'created_at': envelope.dispatch.created_at.isoformat(),
            'updated_at': envelope.dispatch.updated_at.isoformat(),
            'sender': self._serialize_user(envelope.sender),
            'recipient': self._serialize_user(envelope.recipient),
            'direction': 'inbound' if is_inbound else 'outbound',
            'quick_replies': quick_replies,
        }



def _read_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None



def _read_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_recurrence_every(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, str) and not value.strip():
        return 1
    parsed = _read_optional_int(value)
    if parsed is None:
        raise WebAuthError(status=400, message='Некорректный recurrence_every.')
    return parsed


def _read_reminder_kind(value: Any) -> ReminderRuleKind:
    kind_text = (_read_optional_text(value) or ReminderRuleKind.ONE_TIME.value).lower()
    try:
        return ReminderRuleKind(kind_text)
    except ValueError as error:
        raise WebAuthError(status=400, message='Некорректный тип напоминания.') from error


def _read_recurrence_unit(value: Any) -> ReminderIntervalUnit | None:
    unit_text = (_read_optional_text(value) or '').lower()
    if not unit_text:
        return None
    try:
        return ReminderIntervalUnit(unit_text)
    except ValueError as error:
        raise WebAuthError(status=400, message='Некорректная единица повторения.') from error
