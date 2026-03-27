from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from voice_controller.config import load_app_config_with_env
from voice_controller.pipeline import VoicePipeline, warn_if_unsupported_platform


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI Voice Controller：按住说话，松开后识别并粘贴到当前焦点窗口。",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="使用命令行模式（默认启动图形界面）",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="配置文件路径（默认 ./config.yaml）",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="仅加载配置后退出",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="调试日志",
    )
    args = parser.parse_args(argv)

    use_cli = bool(args.cli or args.validate_only)
    if not use_cli:
        try:
            from voice_controller.ui_main import launch_gui
            return int(launch_gui())
        except Exception as e:
            print(f"GUI 启动失败，自动回退到命令行模式: {e}", file=sys.stderr)
            use_cli = True

    try:
        cfg = load_app_config_with_env(args.config)
    except Exception as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 2

    if args.validate_only:
        print("配置加载成功:", args.config.resolve())
        return 0

    _setup_logging(args.verbose)
    warn_if_unsupported_platform()

    log = logging.getLogger(__name__)
    if cfg.asr.mock:
        log.warning(
            "【当前为模拟模式】asr.mock=true：不会连接 ASR，结果固定为占位句。"
            "请在 config.yaml 中设置 asr.mock: false，并填写可访问的 asr.base_url。",
        )
    else:
        log.info(
            "ASR 提供方=%s(%s)，连接: base_url=%s ws_path=%s",
            cfg.asr.provider_key,
            cfg.asr.provider,
            cfg.asr.base_url,
            cfg.asr.ws_path,
        )

    try:
        VoicePipeline(cfg).run()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("已中断")
        return 130
    except Exception:
        logging.getLogger(__name__).exception("运行异常")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
