"""Centralized logging configuration for jira-triage."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any


def get_log_level(env_var: str, default: str = "INFO") -> int:
    """Get log level from environment variable."""
    level_str = os.getenv(env_var, default).upper()
    return getattr(logging, level_str, logging.INFO)


def get_log_path(env_var: str, default_filename: str) -> Path:
    """Get log file path from environment variable with fallback."""
    log_path = os.getenv(env_var)
    if log_path:
        return Path(log_path)
    
    # Default to logs/ directory in project root
    project_root = Path(__file__).parent.parent
    logs_dir = project_root / "logs"
    logs_dir.mkdir(exist_ok=True)
    return logs_dir / default_filename


def setup_logger(
    name: str,
    log_file: Path,
    level: int = logging.INFO,
    format_str: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> logging.Logger:
    """Set up a logger with file rotation."""
    if format_str is None:
        format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # File handler with rotation
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(format_str)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Console handler for INFO and above
    if level <= logging.INFO:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter("%(levelname)s - %(name)s - %(message)s")
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
    
    return logger


def setup_webhook_logging() -> logging.Logger:
    """Set up webhook request logging."""
    level = get_log_level("WEBHOOK_LOG_LEVEL", "INFO")
    log_file = get_log_path("WEBHOOK_LOG_FILE", "webhook.log")
    
    return setup_logger(
        "jira_triage.webhook",
        log_file,
        level,
        "%(asctime)s - WEBHOOK - %(levelname)s - %(message)s",
    )


def setup_auth_logging() -> logging.Logger:
    """Set up authentication logging."""
    level = get_log_level("AUTH_LOG_LEVEL", "INFO") 
    log_file = get_log_path("AUTH_LOG_FILE", "auth.log")
    
    return setup_logger(
        "jira_triage.auth",
        log_file,
        level,
        "%(asctime)s - AUTH - %(levelname)s - %(message)s",
    )


def setup_general_logging() -> logging.Logger:
    """Set up general application logging."""
    level = get_log_level("APP_LOG_LEVEL", "INFO")
    log_file = get_log_path("APP_LOG_FILE", "app.log")
    
    return setup_logger(
        "jira_triage",
        log_file,
        level,
    )


def setup_polling_logging() -> logging.Logger:
    """Set up polling service logging."""
    level = get_log_level("POLLING_LOG_LEVEL", "INFO")
    log_file = get_log_path("POLLING_LOG_FILE", "polling.log")
    
    return setup_logger(
        "jira_triage.polling",
        log_file,
        level,
        "%(asctime)s - POLLING - %(levelname)s - %(message)s",
    )


def configure_all_logging() -> dict[str, logging.Logger]:
    """Configure all loggers and return them."""
    return {
        "webhook": setup_webhook_logging(),
        "auth": setup_auth_logging(), 
        "app": setup_general_logging(),
        "polling": setup_polling_logging(),
    }


def log_request_info(logger: logging.Logger, **kwargs: Any) -> None:
    """Log structured request information."""
    # Filter out sensitive information
    safe_kwargs = {}
    for key, value in kwargs.items():
        if key.lower() in ("authorization", "token", "password", "secret"):
            safe_kwargs[key] = "[REDACTED]"
        else:
            safe_kwargs[key] = value
    
    logger.info("Request info: %s", safe_kwargs)


def log_auth_attempt(logger: logging.Logger, **kwargs: Any) -> None:
    """Log structured authentication attempt information."""
    # Filter out sensitive information
    safe_kwargs = {}
    for key, value in kwargs.items():
        if key.lower() in ("token", "password", "pat", "authorization"):
            if isinstance(value, str) and value:
                safe_kwargs[f"{key}_length"] = len(value)
                safe_kwargs[f"{key}_prefix"] = value[:4] + "..." if len(value) > 4 else "***"
            else:
                safe_kwargs[key] = "[REDACTED]"
        else:
            safe_kwargs[key] = value
    
    logger.info("Auth attempt: %s", safe_kwargs)