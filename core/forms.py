from django import forms


class BootstrapMixin:
    """Добавляет полям классы Bootstrap, чтобы шаблоны оставались простыми."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(widget, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            widget.attrs["class"] = (widget.attrs.get("class", "") + " " + css).strip()
