from dataclasses import dataclass

from backend.app.runtimes.models import RuntimeTemplate


class RuntimeSafetyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RuntimeSafetyPolicy:
    allowed_images: tuple[str, ...]

    def assert_template_allowed(self, template: RuntimeTemplate) -> None:
        if template.status != "active":
            raise RuntimeSafetyError(
                "runtime_template_inactive",
                "Runtime template is not active",
            )
        if template.image not in self.allowed_images:
            raise RuntimeSafetyError(
                "runtime_image_not_allowed",
                "Runtime image is not allowed by personal safety settings",
            )

    def assert_network_allowed(
        self,
        template: RuntimeTemplate,
        *,
        network_disabled: bool,
    ) -> None:
        if network_disabled:
            return
        if template.default_network_policy.get("allow_network") is True:
            return
        raise RuntimeSafetyError(
            "runtime_network_not_allowed",
            "Runtime network access is disabled by default for personal safety",
        )
