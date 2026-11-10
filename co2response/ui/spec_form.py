"""A settings form built from a field list in ``co2response/forms.py`` (desktop).

The browser page builds the same forms from the same lists (``web/spec_form.py``).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Sequence

from PySide6.QtWidgets import QCheckBox, QLabel

from ..forms import Field, patch_for
from .widgets import Form, NumberEdit, Seg, toggle


class SpecForm(Form):
    """``get()`` returns the settings object being edited; ``apply(patch)`` saves a
    change to it. ``controls[name]`` is each field's widget."""

    def __init__(self, fields: Sequence[Field], get: Callable[[], Any], apply: Callable[[dict], None]) -> None:
        super().__init__()
        self.fields = tuple(fields)
        self._get = get
        self._apply = apply
        self.controls: dict[str, Any] = {}
        self.switches: dict[str, QCheckBox] = {}
        self._hints: dict[str, QLabel] = {}
        obj = get()
        for f in self.fields:
            self._build(f, obj)
        self.refresh(obj)

    def _build(self, f: Field, obj: Any) -> None:
        cur = getattr(obj, f.key)
        hint = f.hint_for(obj) or (" " if f.kind == "choice" or f.live else "")
        if f.kind == "toggle":
            w = toggle(bool(cur), lambda on, f=f: self._edit(f, on=on))
            self.add(f.name, f.label, w, hint=hint or None)
        elif f.kind == "choice":
            w = Seg(list(f.options), cur)
            w.changed.connect(lambda v, f=f: self._edit(f, choice=v))
            self.add(f.name, f.label, w, hint=hint or None)
        elif f.kind == "optional":
            sw = toggle(cur is not None, lambda on, f=f: self._edit(f, on=on))
            self.switches[f.name] = sw
            self.add(f"{f.name}on", f.label, sw, hint=hint or None)
            w = self._number(f, cur if cur is not None else f.default)
            self.add(f.name, f.value_label or f.label, w, f.unit)
        else:
            w = self._number(f, cur)
            self.add(f.name, f.label, w, f.unit or None, hint or None)
        self.controls[f.name] = w
        key = f"{f.name}on" if f.kind == "optional" else f.name
        if hint:
            self._hints[f.name] = self.rows[key][2]

    def _number(self, f: Field, stored) -> NumberEdit:
        ne = NumberEdit(f.to_display(stored), f.lo, f.hi, f.step, allow_blank=f.kind == "blank")
        ne.committed.connect(lambda v, f=f: self._edit(f, shown=v))
        ne.cleared.connect(lambda f=f: self._edit(f, shown=None))
        ne.setAccessibleName(f.value_label or f.label)
        return ne

    def _edit(self, f: Field, **change) -> None:
        patch = patch_for(f, self._get(), **change)
        if patch:
            self._apply(patch)
        self.refresh(self._get())

    def refresh(self, obj: Any = None, snapshot: Any = None) -> None:
        """Show the settings as they are now (fields being typed in are left alone)."""
        obj = self._get() if obj is None else obj
        for f in self.fields:
            cur = getattr(obj, f.key)
            w = self.controls[f.name]
            shown = f.shown(obj)
            if f.kind == "toggle":
                _set_checked(w, bool(cur))
            elif f.kind == "choice":
                w.set_value(cur)
            elif f.kind == "optional":
                _set_checked(self.switches[f.name], cur is not None)
                self.show_row(f"{f.name}on", shown)
                if cur is not None:
                    w.set(f.to_display(cur))
                self.show_row(f.name, shown and cur is not None)
                shown = None
            else:
                v = f.to_display(cur) if cur is not None and math.isfinite(cur) else None
                w.set(v)
            if shown is not None:
                self.show_row(f.name, shown)
            lab = self._hints.get(f.name)
            if lab is not None:
                text = f.hint_for(obj) + (f.live(snapshot) if f.live and snapshot is not None else "")
                if text.strip() and lab.text() != text:
                    lab.setText(text)


def _set_checked(cb: QCheckBox, on: bool) -> None:
    if cb.isChecked() != on:
        cb.blockSignals(True)
        cb.setChecked(on)
        cb.blockSignals(False)
