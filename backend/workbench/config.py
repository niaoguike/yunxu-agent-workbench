import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('WORKBENCH_DATA', '.data')).resolve())
    runner: str = field(default_factory=lambda: os.getenv('WORKBENCH_RUNNER', 'disabled'))
    codex_command: str = field(default_factory=lambda: os.getenv('CODEX_COMMAND', 'codex'))
    docker_image: str = field(default_factory=lambda: os.getenv('WORKBENCH_IMAGE', 'agent-workbench-runner:local'))
    approval_timeout: int = 300
    secure_cookies: bool = field(default_factory=lambda: os.getenv('WORKBENCH_SECURE_COOKIES', '0') == '1')
    origin: str = field(default_factory=lambda: os.getenv('WORKBENCH_ORIGIN', 'http://localhost:5173'))
    max_upload: int = 20 * 1024 * 1024
    max_run_seconds: int = 1800

