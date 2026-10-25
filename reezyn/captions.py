"""A quiet caption viewer for the line Reezyn is speaking.

The viewer is a borderless, non-activating panel: it floats at the top of the
screen, a third of the screen wide, and never takes the keyboard away from the
editor. Its glass is an `NSVisualEffectView`, so it tracks the desktop and the
system appearance instead of painting a colour of its own.

Nothing here reads the agent or the transcript. The menu already holds the two
facts this needs — the line being spoken and the last few lines — and the panel
only decides how to lay them out.
"""

from __future__ import annotations

import logging
import math

from .config import CAPTION_FONTS, CaptionsConfig

log = logging.getLogger("reezyn")

WIDTH_RATIO = 1 / 3
MIN_WIDTH = 440.0
EDGE_MARGIN = 12.0
PADDING = 16.0
ROW_GAP = 6.0
CONTROL_HEIGHT = 18.0
CONTROL_GAP = 10.0
CORNER_RADIUS = 14.0
FADED_SCALE = 0.8
FADED_ALPHA = 0.45
LIVE_ALPHA = 0.94
CONTROL_ALPHA = 0.62
MAX_HISTORY = 2
# The viewer may never grow past a third of the screen, however long the line.
HEIGHT_RATIO = 1 / 3
HOTKEY_KEYCODE = 4  # H, by physical key so the layout does not matter
OPTION_KEYCODE = 58  # kVK_Option
HID_SYSTEM_STATE = 0
CLICK_THROUGH_POLL_S = 0.06
FONT_STEP_SIZES = sorted(CAPTION_FONTS.values())

_ACTIONS_CLASS = None


def clamp_opacity(value: float) -> float:
    return max(0.3, min(1.0, float(value)))


def nearest_font_size(value: float) -> float:
    return min(FONT_STEP_SIZES, key=lambda size: abs(size - float(value)))


def step_font_size(current: float, direction: int) -> float:
    """The next size up or down, staying inside the three choices."""

    index = FONT_STEP_SIZES.index(nearest_font_size(current))
    index = max(0, min(len(FONT_STEP_SIZES) - 1, index + (1 if direction > 0 else -1)))
    return FONT_STEP_SIZES[index]


def panel_frame(
    screen_width: float,
    screen_height: float,
    usable_top: float,
    height: float,
) -> tuple[float, float, float, float]:
    """A centred, top-anchored frame a third of the screen wide."""

    width = max(MIN_WIDTH, screen_width * WIDTH_RATIO)
    width = min(width, max(MIN_WIDTH, screen_width - 2.0 * EDGE_MARGIN))
    height = max(24.0, min(height, screen_height * HEIGHT_RATIO))
    x = (screen_width - width) / 2.0
    y = usable_top - EDGE_MARGIN - height
    return x, y, width, height


def caption_rows(
    current: str,
    recent: list[str],
    collapsed: bool = False,
) -> list[tuple[str, bool]]:
    """Rows to draw, oldest first; the last entry is the live line.

    The live line is what is being spoken now; between thoughts the most recent
    line takes its place, so the panel never flashes empty.
    """

    live = " ".join((current or "").split())
    history = [" ".join(line.split()) for line in recent if line and line.strip()]
    history = [line for line in history if line != live]

    if collapsed:
        text = live or (history[0] if history else "")
        return [(text, True)] if text else []

    if live:
        faded = history[:MAX_HISTORY][::-1]
        return [(line, False) for line in faded] + [(live, True)]

    return [
        (line, index == 0) for index, line in enumerate(history[: MAX_HISTORY + 1])
    ]


def _actions_class() -> type:
    """One Objective-C class for every button, built on first use."""

    global _ACTIONS_CLASS
    if _ACTIONS_CLASS is not None:
        return _ACTIONS_CLASS

    import objc
    from Foundation import NSObject

    class _CaptionActions(NSObject):
        def initWithOwner_(self, owner):
            self = objc.super(_CaptionActions, self).init()
            if self is None:
                return None
            self.owner = owner
            return self

        def pause_(self, _sender):
            self.owner.toggle_pause()

        def skip_(self, _sender):
            self.owner.skip()

        def copyLine_(self, _sender):
            self.owner.copy_line()

        def smaller_(self, _sender):
            self.owner.step_font(-1)

        def larger_(self, _sender):
            self.owner.step_font(1)

        def collapse_(self, _sender):
            self.owner.toggle_collapsed()

        def hideViewer_(self, _sender):
            self.owner.hide_from_panel()

        def opacity_(self, sender):
            self.owner.set_opacity(float(sender.doubleValue()))

    _ACTIONS_CLASS = _CaptionActions
    return _CaptionActions


class CaptionOverlay:
    """The panel, its controls, and the two monitors that serve it."""

    def __init__(
        self,
        config: CaptionsConfig,
        on_change=None,
        on_pause=None,
        on_skip=None,
    ) -> None:
        self.config = config
        self._on_change = on_change
        self._on_pause = on_pause
        self._on_skip = on_skip
        self._panel = None
        self._effect = None
        self._fields: list = []
        self._controls: dict[str, object] = {}
        self._actions = None
        self._hotkey = None
        self._poll = None
        self._rows: list[tuple[str, bool]] = []
        self._signature: tuple | None = None
        # Where the panel currently sits, and how far it has been dragged from
        # its default spot. A drag survives the next line instead of snapping
        # back, but a display change starts from the default again.
        self._placed: tuple[float, float] | None = None
        self._placed_top = 0.0
        self._offset = (0.0, 0.0)
        self._screen_key: tuple[float, float, float] | None = None
        self._visible = False
        self._paused = False
        self._persisted_opacity = clamp_opacity(config.opacity)
        self._error = ""

    # -- lifecycle ---------------------------------------------------------

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def error(self) -> str:
        return self._error

    def show(self) -> bool:
        if not self._ensure_panel():
            return False
        self._visible = True
        self._signature = None
        self._render()
        self._panel.orderFrontRegardless()
        self._install_hotkey()
        self._watch_option()
        log.info("caption viewer shown")
        return True

    def hide(self) -> None:
        if self._visible:
            log.info("caption viewer hidden")
        self._visible = False
        if self._panel is not None:
            self._panel.orderOut_(None)
        self._stop_option_watch()

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    def hide_from_panel(self) -> None:
        """The ✕ button: turn the feature off, not just hide the window."""

        self.config.enabled = False
        self.hide()
        self._changed()

    def close(self) -> None:
        self._stop_hotkey()
        self._stop_option_watch()
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel.close()
            self._panel = None
        self._visible = False

    # -- content -----------------------------------------------------------

    def refresh(self) -> None:
        """Redraw after the menu changed a setting this panel renders."""

        self._signature = None
        self._render()
        self._sync_controls()

    def update(self, current: str, recent: list[str], paused: bool = False) -> None:
        if paused != self._paused:
            self._paused = paused
            self._sync_controls()
        if not self._visible:
            return
        rows = caption_rows(current, recent, self.config.collapsed)
        if rows != self._rows:
            self._rows = rows
        # Cheap when nothing changed, but it also re-anchors the panel if the
        # screen moved or the resolution changed under it.
        self._render()

    # -- controls ----------------------------------------------------------

    def toggle_pause(self) -> None:
        if self._on_pause is not None:
            self._on_pause()
        self._sync_controls()

    def skip(self) -> None:
        if self._on_skip is not None:
            self._on_skip()
        self._sync_controls()

    def copy_line(self) -> None:
        text = self._live_text()
        if not text:
            return
        try:
            from AppKit import NSPasteboard, NSPasteboardTypeString

            board = NSPasteboard.generalPasteboard()
            board.clearContents()
            board.setString_forType_(text, NSPasteboardTypeString)
        except Exception:
            log.debug("could not copy the caption line", exc_info=True)

    def step_font(self, direction: int) -> None:
        self.config.font_size = step_font_size(self.config.font_size, direction)
        self._signature = None
        self._render()
        self._changed()

    def set_opacity(self, value: float) -> None:
        opacity = clamp_opacity(value)
        self.config.opacity = opacity
        if self._panel is not None:
            self._panel.setAlphaValue_(opacity)
        # A drag fires continuously; write the config when it has moved enough
        # to matter rather than on every pixel.
        if abs(opacity - self._persisted_opacity) >= 0.04:
            self._persisted_opacity = opacity
            self._changed()

    def toggle_collapsed(self) -> None:
        self.config.collapsed = not self.config.collapsed
        self._rows = caption_rows(
            self._rows[-1][0] if self._rows else "", [], self.config.collapsed
        )
        self._signature = None
        self._render()
        self._changed()

    def toggle_click_through(self) -> None:
        self.config.click_through = not self.config.click_through
        self._apply_mouse()
        if self.config.click_through:
            self._watch_option()
        else:
            self._stop_option_watch()
        self._changed()

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                log.debug("caption change callback failed", exc_info=True)

    def _live_text(self) -> str:
        for text, active in reversed(self._rows):
            if active:
                return text
        return ""

    # -- construction ------------------------------------------------------

    def _ensure_panel(self) -> bool:
        if self._panel is not None:
            return True
        try:
            self._build()
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            log.warning("could not build the caption viewer: %s", exc)
            self._panel = None
            return False
        return self._panel is not None

    def _build(self) -> None:
        from AppKit import (
            NSBackingStoreBuffered,
            NSButton,
            NSColor,
            NSFloatingWindowLevel,
            NSFont,
            NSFontAttributeName,
            NSFontWeightMedium,
            NSForegroundColorAttributeName,
            NSMakeRect,
            NSMutableParagraphStyle,
            NSPanel,
            NSSlider,
            NSTextAlignmentCenter,
            NSTextField,
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectMaterialHUDWindow,
            NSVisualEffectStateActive,
            NSVisualEffectView,
            NSWindowAnimationBehaviorNone,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
        )

        width = MIN_WIDTH
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, 120),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setAnimationBehavior_(NSWindowAnimationBehaviorNone)
        panel.setMovableByWindowBackground_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )

        effect = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 120))
        effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(CORNER_RADIUS)
        effect.layer().setMasksToBounds_(True)
        effect.layer().setBorderWidth_(0.5)
        try:
            from Quartz import CGColorCreateGenericGray

            effect.layer().setBorderColor_(CGColorCreateGenericGray(1.0, 0.12))
        except Exception:
            log.debug("could not tint the caption border", exc_info=True)
        panel.setContentView_(effect)

        # Everything _attributed() needs lives on the instance, so it has to be
        # in place before the first button asks for a title.
        self._panel = panel
        self._effect = effect
        self._rect = NSMakeRect
        self._nsfont = NSFont
        self._font_key = NSFontAttributeName
        self._color_key = NSForegroundColorAttributeName
        self._font_medium = NSFontWeightMedium
        self._paragraph = NSMutableParagraphStyle.alloc().init()
        self._paragraph.setAlignment_(NSTextAlignmentCenter)

        self._actions = _actions_class().alloc().initWithOwner_(self)
        self._fields = [self._make_field() for _ in range(MAX_HISTORY + 1)]
        for field in self._fields:
            effect.addSubview_(field)

        def glyph(title: str, label: str, selector: bytes) -> object:
            button = NSButton.alloc().initWithFrame_(
                NSMakeRect(0, 0, 24, CONTROL_HEIGHT)
            )
            button.setBordered_(False)
            button.setTarget_(self._actions)
            button.setAction_(selector)
            button.setAccessibilityLabel_(label)
            button.setAttributedTitle_(
                self._attributed(title, NSFont.systemFontOfSize_(12.5), CONTROL_ALPHA)
            )
            effect.addSubview_(button)
            return button

        self._controls = {
            "pause": glyph("\u23f8", "Pause or resume narration", b"pause:"),
            "skip": glyph("\u23ed", "Skip this thought", b"skip:"),
            "copy": glyph("\u29c9", "Copy the current line", b"copyLine:"),
            "smaller": glyph("A\u2212", "Smaller text", b"smaller:"),
            "larger": glyph("A+", "Larger text", b"larger:"),
            "collapse": glyph("\u25be", "Collapse to a pill", b"collapse:"),
            "hide": glyph("\u2715", "Hide the CC viewer", b"hideViewer:"),
        }

        slider = NSSlider.alloc().initWithFrame_(
            NSMakeRect(0, 0, 84, CONTROL_HEIGHT)
        )
        slider.setMinValue_(0.3)
        slider.setMaxValue_(1.0)
        slider.setDoubleValue_(clamp_opacity(self.config.opacity))
        slider.setContinuous_(True)
        slider.setTarget_(self._actions)
        slider.setAction_(b"opacity:")
        slider.setAccessibilityLabel_("Caption opacity")
        effect.addSubview_(slider)
        self._controls["opacity"] = slider

        panel.setAlphaValue_(clamp_opacity(self.config.opacity))
        self._apply_mouse()
        self._sync_controls()

    def _make_field(self) -> object:
        from AppKit import NSMakeRect, NSTextAlignmentCenter, NSTextField

        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setAlignment_(NSTextAlignmentCenter)
        field.cell().setWraps_(True)
        field.cell().setLineBreakMode_(0)  # wrap at word boundaries
        return field

    def _attributed(self, text: str, font: object, alpha: float) -> object:
        from AppKit import NSAttributedString, NSColor

        attributes = {
            self._font_key: font,
            self._color_key: NSColor.whiteColor().colorWithAlphaComponent_(alpha),
            "NSParagraphStyle": self._paragraph,
        }
        return NSAttributedString.alloc().initWithString_attributes_(text, attributes)

    def _set_button_title(self, name: str, title: str, label: str) -> None:
        button = self._controls.get(name)
        if button is None:
            return
        button.setAttributedTitle_(
            self._attributed(title, self._nsfont.systemFontOfSize_(12.5), CONTROL_ALPHA)
        )
        button.setAccessibilityLabel_(label)

    def _sync_controls(self) -> None:
        if not self._controls:
            return
        try:
            self._set_button_title(
                "pause",
                "\u25b6" if self._paused else "\u23f8",
                "Resume narration" if self._paused else "Pause narration",
            )
            self._set_button_title(
                "collapse",
                "\u25b4" if self.config.collapsed else "\u25be",
                "Expand the CC viewer"
                if self.config.collapsed
                else "Collapse to a pill",
            )
            slider = self._controls.get("opacity")
            if slider is not None:
                slider.setDoubleValue_(clamp_opacity(self.config.opacity))
        except Exception:
            log.debug("could not refresh caption controls", exc_info=True)

    # -- layout ------------------------------------------------------------

    def _measure(self, text: str, width: float, font: object) -> float:
        from AppKit import (
            NSAttributedString,
            NSFontAttributeName,
            NSStringDrawingUsesLineFragmentOrigin,
        )
        from Foundation import NSMakeSize

        string = NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font}
        )
        rect = string.boundingRectWithSize_options_(
            NSMakeSize(width, 4000.0), NSStringDrawingUsesLineFragmentOrigin
        )
        return float(math.ceil(rect.size.height)) + 1.0

    def _render(self) -> None:
        if self._panel is None:
            return
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        if screen is None:
            return
        screen_frame = screen.visibleFrame()
        screen_width = float(screen_frame.size.width)
        screen_height = float(screen_frame.size.height)
        usable_top = float(screen_frame.origin.y + screen_frame.size.height)

        collapsed = self.config.collapsed
        signature = (
            tuple(self._rows),
            collapsed,
            round(self.config.font_size, 1),
            round(screen_width, 1),
            round(usable_top, 1),
        )
        if signature == self._signature:
            return

        _, _, width, _ = panel_frame(screen_width, screen_height, usable_top, 0.0)
        text_width = width - 2.0 * PADDING
        live_size = nearest_font_size(self.config.font_size)
        faded_size = max(11.0, live_size * FADED_SCALE)

        rows = list(self._rows)
        if collapsed:
            rows = [(rows[-1][0], True)] if rows else []
        elif not rows:
            # A quiet panel should say what it is waiting for, not show a strip
            # of glass with nothing in it.
            rows = [("Listening for agent reasoning", False)]

        def font_for(active: bool) -> object:
            return self._nsfont.systemFontOfSize_weight_(
                live_size if active else faded_size,
                self._font_medium if active else 0.0,
            )

        measured: list[tuple[str, bool, float]] = []
        for text, active in rows:
            if collapsed:
                font = font_for(active)
                height = max(18.0, math.ceil(font.ascender() - font.descender() + 2.0))
            else:
                height = self._measure(text, text_width, font_for(active))
            measured.append((text, active, height))

        max_height = screen_height * HEIGHT_RATIO
        while not collapsed and len(measured) > 1:
            total = (
                2.0 * PADDING
                + sum(item[2] for item in measured)
                + ROW_GAP * (len(measured) - 1)
                + CONTROL_HEIGHT
                + CONTROL_GAP
            )
            if total <= max_height:
                break
            measured.pop(0)

        controls_height = 0.0 if collapsed else CONTROL_HEIGHT + CONTROL_GAP
        # A single very long line is clipped rather than allowed to push the
        # panel past its third of the screen.
        room = (
            max_height
            - 2.0 * PADDING
            - controls_height
            - ROW_GAP * max(0, len(measured) - 1)
        )
        if measured:
            others = sum(item[2] for item in measured[:-1])
            text, active, height = measured[-1]
            measured[-1] = (text, active, min(height, max(18.0, room - others)))

        total_height = (
            2.0 * PADDING
            + sum(item[2] for item in measured)
            + ROW_GAP * max(0, len(measured) - 1)
            + controls_height
        )
        total_height = max(total_height, PADDING * 2.0 + 24.0)

        x, y, width, total_height = panel_frame(
            screen_width, screen_height, usable_top, total_height
        )
        screen_key = (round(screen_width, 1), round(screen_height, 1), round(usable_top, 1))
        if screen_key != self._screen_key:
            self._offset = (0.0, 0.0)
            self._placed = None
        else:
            self._remember_drag(x, y + total_height)
        x += self._offset[0]
        y += self._offset[1]
        self._panel.setFrame_display_(
            self._rect(x, y, width, total_height), True
        )
        self._effect.setFrame_(self._rect(0, 0, width, total_height))
        self._placed = (x, y)
        self._placed_top = y + total_height
        self._screen_key = screen_key

        cursor = total_height - PADDING
        for index, field in enumerate(self._fields):
            if index >= len(measured):
                field.setStringValue_("")
                field.setHidden_(True)
                continue
            text, active, height = measured[index]
            field.setHidden_(False)
            field.setFrame_(
                self._rect(PADDING, cursor - height, text_width, height)
            )
            field.cell().setLineBreakMode_(4 if collapsed else 0)
            field.setAttributedStringValue_(
                self._attributed(text, font_for(active), LIVE_ALPHA if active else FADED_ALPHA)
            )
            cursor -= height + ROW_GAP

        self._layout_controls(width, collapsed)
        self._signature = signature

    def _remember_drag(self, default_x: float, default_top: float) -> None:
        """Keep a panel the developer dragged where they put it."""

        if self._placed is None:
            return
        current = self._panel.frame()
        moved_x = abs(current.origin.x - self._placed[0]) > 1.0
        moved_top = abs((current.origin.y + current.size.height) - self._placed_top) > 1.0
        if not (moved_x or moved_top):
            return
        self._offset = (
            current.origin.x - default_x,
            (current.origin.y + current.size.height) - default_top,
        )

    def _layout_controls(self, width: float, collapsed: bool) -> None:
        if not self._controls:
            return

        y = PADDING * 0.65
        if collapsed:
            for name, button in self._controls.items():
                button.setHidden_(name not in {"collapse", "hide"})
            right = width - PADDING - 24.0
            for name in ("hide", "collapse"):
                button = self._controls.get(name)
                if button is not None:
                    button.setFrame_(self._rect(right, y, 24.0, CONTROL_HEIGHT))
                    right -= 28.0
            return

        for name in ("pause", "skip", "copy", "smaller", "larger", "collapse", "hide", "opacity"):
            button = self._controls.get(name)
            if button is not None:
                button.setHidden_(False)

        x = PADDING
        for name in ("pause", "skip", "copy"):
            button = self._controls.get(name)
            if button is not None:
                button.setFrame_(self._rect(x, y, 24.0, CONTROL_HEIGHT))
                x += 26.0

        right = width - PADDING
        for name in ("hide", "collapse", "larger", "smaller"):
            button = self._controls.get(name)
            if button is None:
                continue
            right -= 24.0
            button.setFrame_(self._rect(right, y, 24.0, CONTROL_HEIGHT))
            right -= 4.0
        slider = self._controls.get("opacity")
        if slider is not None:
            right -= 6.0 + 84.0
            slider.setFrame_(self._rect(right, y, 84.0, CONTROL_HEIGHT))

    # -- mouse and hotkey --------------------------------------------------

    def _apply_mouse(self) -> None:
        if self._panel is None:
            return
        try:
            # Click-through panels ignore the pointer until Option is held.
            self._panel.setIgnoresMouseEvents_(bool(self.config.click_through))
        except Exception:
            log.debug("could not apply click-through", exc_info=True)

    def _watch_option(self) -> None:
        if not self.config.click_through or self._poll is not None:
            return
        try:
            from AppKit import NSTimer

            self._poll = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                CLICK_THROUGH_POLL_S, True, lambda _timer: self._poll_option()
            )
        except Exception:
            log.debug("could not start the modifier watch", exc_info=True)

    def _poll_option(self) -> None:
        if not self._visible or not self.config.click_through or self._panel is None:
            self._stop_option_watch()
            return
        try:
            from Quartz import CGEventSourceKeyState

            held = bool(CGEventSourceKeyState(HID_SYSTEM_STATE, OPTION_KEYCODE))
        except Exception:
            held = False
        self._panel.setIgnoresMouseEvents_(not held)

    def _stop_option_watch(self) -> None:
        timer = self._poll
        self._poll = None
        if timer is not None:
            try:
                timer.invalidate()
            except Exception:
                pass
        if self._panel is not None and not self.config.click_through:
            self._apply_mouse()

    def _install_hotkey(self) -> None:
        if self._hotkey is not None:
            return
        try:
            from AppKit import NSEvent, NSEventMaskKeyDown

            self._hotkey = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown, self._on_hotkey
            )
        except Exception as exc:
            self._error = f"hotkey unavailable: {exc}"
            log.debug("could not install the caption hotkey", exc_info=True)

    def _on_hotkey(self, event) -> None:
        try:
            from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagOption

            if event.keyCode() != HOTKEY_KEYCODE:
                return
            flags = event.modifierFlags()
            if not flags & NSEventModifierFlagCommand:
                return
            if not flags & NSEventModifierFlagOption:
                return
            self.toggle()
        except Exception:
            log.debug("caption hotkey handler failed", exc_info=True)

    def _stop_hotkey(self) -> None:
        monitor = self._hotkey
        self._hotkey = None
        if monitor is None:
            return
        try:
            from AppKit import NSEvent

            NSEvent.removeMonitor_(monitor)
        except Exception:
            pass
