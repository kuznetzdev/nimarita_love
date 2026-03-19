from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from nimarita.config import Settings
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import (
    AuditRepository,
    CareRepository,
    EphemeralMessageRepository,
    PairingRepository,
    ReminderRepository,
    UIPanelRepository,
    UserRepository,
)
from nimarita.services import (
    AccessPolicy,
    AuditService,
    CareService,
    HeartbeatRegistry,
    PairingService,
    ReminderService,
    SystemService,
    UserService,
)
from nimarita.telegram.notifier import TelegramNotifier
from nimarita.telegram.router import build_router
from nimarita.telegram.ui import TelegramUI
from nimarita.web.server import WebServer
from nimarita.workers.care import CareWorker
from nimarita.workers.cleanup import CleanupWorker
from nimarita.workers.maintenance import MaintenanceWorker
from nimarita.workers.reminders import ReminderWorker


@dataclass(slots=True)
class Runtime:
    settings: Settings
    bot: Bot
    dispatcher: Dispatcher
    database: SQLiteDatabase
    users: UserRepository
    pairing_repo: PairingRepository
    reminder_repo: ReminderRepository
    care_repo: CareRepository
    audit_repo: AuditRepository
    ui_panels: UIPanelRepository
    ephemeral_repo: EphemeralMessageRepository
    user_service: UserService
    pairing_service: PairingService
    reminder_service: ReminderService
    care_service: CareService
    audit_service: AuditService
    system_service: SystemService
    ui: TelegramUI
    notifier: TelegramNotifier
    web_server: WebServer
    reminder_worker: ReminderWorker
    care_worker: CareWorker
    cleanup_worker: CleanupWorker
    maintenance_worker: MaintenanceWorker

    async def start(self) -> None:
        await self.bot.get_me()
        await self.system_service.reconcile_startup()
        await self.system_service.run_startup_database_audit()
        if self.settings.backup_enabled and self.settings.backup_on_startup:
            await self.system_service.create_backup(reason='startup')
        await self.reminder_worker.start()
        await self.care_worker.start()
        await self.cleanup_worker.start()
        await self.maintenance_worker.start()
        if self.settings.webapp_enabled:
            await self.web_server.start()

    async def close(self) -> None:
        await self.web_server.stop()
        await self.maintenance_worker.stop()
        await self.cleanup_worker.stop()
        await self.care_worker.stop()
        await self.reminder_worker.stop()
        try:
            await self.system_service.graceful_shutdown()
        finally:
            await self.database.close()
            await self.bot.session.close()


async def build_runtime(settings: Settings) -> Runtime:
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()

    database = SQLiteDatabase(
        settings.database_path,
        synchronous=settings.sqlite_synchronous,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        wal_autocheckpoint_pages=settings.sqlite_wal_autocheckpoint_pages,
        journal_size_limit_bytes=settings.sqlite_journal_size_limit_bytes,
    )
    await database.connect()

    users = UserRepository(database, default_timezone=settings.default_timezone)
    pairing_repo = PairingRepository(database)
    reminder_repo = ReminderRepository(database)
    care_repo = CareRepository(database)
    audit_repo = AuditRepository(database)
    ui_panels = UIPanelRepository(database)
    ephemeral_repo = EphemeralMessageRepository(database)

    links = LinkBuilder(settings)
    heartbeats = HeartbeatRegistry()
    audit_service = AuditService(audit_repo)
    access_policy = AccessPolicy(settings)
    user_service = UserService(users, access=access_policy, audit=audit_service)
    pairing_service = PairingService(
        pairing=pairing_repo,
        users=users,
        settings=settings,
        links=links,
        reminders=reminder_repo,
        audit=audit_service,
    )
    reminder_service = ReminderService(
        reminders=reminder_repo,
        pairing=pairing_repo,
        users=users,
        settings=settings,
        audit=audit_service,
    )
    care_service = CareService(
        care=care_repo,
        pairing=pairing_repo,
        users=users,
        settings=settings,
        audit=audit_service,
    )
    await care_service.ensure_seeded()

    ui = TelegramUI(bot=bot, panels=ui_panels, ephemeral=ephemeral_repo)
    notifier = TelegramNotifier(bot, ui, settings)
    system_service = SystemService(
        settings=settings,
        database=database,
        pairing=pairing_repo,
        reminders=reminder_repo,
        care=care_repo,
        heartbeats=heartbeats,
        audit=audit_service,
    )

    dispatcher.include_router(
        build_router(
            settings=settings,
            user_service=user_service,
            pairing_service=pairing_service,
            reminder_service=reminder_service,
            care_service=care_service,
            notifier=notifier,
            ui=ui,
        )
    )
    await bot.set_my_commands(
        [
            BotCommand(command='start', description='Запустить бота и зарегистрироваться'),
            BotCommand(command='open', description='Открыть Mini App'),
            BotCommand(command='pair', description='Создать приглашение в пару'),
            BotCommand(command='status', description='Показать состояние пары'),
            BotCommand(command='remind', description='Быстро поставить one-time reminder'),
            BotCommand(command='care', description='Открыть care layer и шаблоны заботы'),
            BotCommand(command='help', description='Справка'),
            BotCommand(command='unpair', description='Завершить активную пару'),
        ]
    )

    web_server = WebServer(
        settings=settings,
        user_service=user_service,
        pairing_service=pairing_service,
        reminder_service=reminder_service,
        care_service=care_service,
        notifier=notifier,
        audit=audit_service,
        system=system_service,
    )
    reminder_worker = ReminderWorker(
        settings=settings,
        reminders=reminder_service,
        notifier=notifier,
        heartbeats=heartbeats,
    )
    care_worker = CareWorker(
        settings=settings,
        care=care_service,
        notifier=notifier,
        heartbeats=heartbeats,
    )
    cleanup_worker = CleanupWorker(settings=settings, ui=ui, heartbeats=heartbeats)
    maintenance_worker = MaintenanceWorker(settings=settings, system=system_service, heartbeats=heartbeats)

    return Runtime(
        settings=settings,
        bot=bot,
        dispatcher=dispatcher,
        database=database,
        users=users,
        pairing_repo=pairing_repo,
        reminder_repo=reminder_repo,
        care_repo=care_repo,
        audit_repo=audit_repo,
        ui_panels=ui_panels,
        ephemeral_repo=ephemeral_repo,
        user_service=user_service,
        pairing_service=pairing_service,
        reminder_service=reminder_service,
        care_service=care_service,
        audit_service=audit_service,
        system_service=system_service,
        ui=ui,
        notifier=notifier,
        web_server=web_server,
        reminder_worker=reminder_worker,
        care_worker=care_worker,
        cleanup_worker=cleanup_worker,
        maintenance_worker=maintenance_worker,
    )
