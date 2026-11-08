"""After the nightly run: alert cards and source-failure notices."""

from __future__ import annotations

from tripbot import i18n
from tripbot.domain.alerts import Alert
from tripbot.pipeline.nightly import NightlyReport
from tripbot.telegram.api import Messenger
from tripbot.telegram.commands import details_button
from tripbot.telegram.context import BotContext
from tripbot.telegram.format import bold, esc

SOURCE_NAMES = {"google": "Google Flights", "serpapi": "SerpApi", "tripcom": "Trip.com"}


def notify_nightly(
    messenger: Messenger, chat_id: str, report: NightlyReport, context: BotContext
) -> None:
    if report.alerts:
        with context.open_deck(context) as deck:
            for alert in report.alerts:
                png = deck.alert(alert)
                if png is not None:
                    messenger.send_photo(chat_id, png, None, details_button(context))
                else:
                    messenger.send_message(chat_id, alert_text(alert))
    if report.newly_failing:
        names = ", ".join(SOURCE_NAMES.get(name, name) for name in report.newly_failing)
        notice = bold(
            i18n._("Source down: {sources}; failing two runs in a row.").format(sources=names)
        )
        messenger.send_message(chat_id, notice)


def alert_text(alert: Alert) -> str:
    heading = (
        i18n._("New record low · window {window}")
        if alert.kind == "record_low"
        else i18n._("Price drop · window {window}")
    ).format(window=alert.window_id)
    return f"{bold(heading)}\n{esc(i18n.money(alert.total))} (−{esc(i18n.money(alert.saving))})"
