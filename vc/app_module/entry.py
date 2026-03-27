from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from vc.config import load_app_config_with_env
from vc.core_module.pipeline import VoicePipeline, warn_if_unsupported_platform
from vc.lexicon_module.service import LexiconStore


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI Voice Controller：按住说话，松开后识别并粘贴到当前焦点窗口。",
    )
    parser.add_argument("--cli", action="store_true", help="使用命令行模式（默认启动图形界面）")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="配置文件路径（默认 ./config.yaml）",
    )
    parser.add_argument("--validate-only", action="store_true", help="仅加载配置后退出")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    parser.add_argument("--lexicon-add", type=str, default="", help="新增/更新术语（配合 --lexicon-aliases 使用）")
    parser.add_argument("--lexicon-aliases", type=str, default="", help="术语别名，逗号分隔，例如：朗链,郎圈")
    parser.add_argument("--lexicon-domain", type=str, default="", help="词库领域（默认取配置 lexicon.domain）")
    parser.add_argument("--lexicon-import-csv", type=Path, default=None, help="从 CSV 导入词库，列：term,aliases,domain,weight")
    parser.add_argument("--lexicon-list", action="store_true", help="列出当前领域词库统计")
    parser.add_argument(
        "--lexicon-export-csv",
        type=Path,
        default=None,
        help="导出当前领域词库到 CSV（列：term,aliases,domain,weight）",
    )
    args = parser.parse_args(argv)

    use_lexicon_ops = bool(
        args.lexicon_list
        or args.lexicon_import_csv
        or args.lexicon_export_csv
        or (args.lexicon_add and args.lexicon_add.strip())
    )
    use_cli = bool(args.cli or args.validate_only or use_lexicon_ops)
    if not use_cli:
        try:
            from vc.ui_module.main_window import launch_gui

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

    if use_lexicon_ops:
        store = LexiconStore(cfg.lexicon.db_path)
        store.ensure_schema()
        active_domain = args.lexicon_domain.strip() or cfg.lexicon.domain

        if args.lexicon_import_csv:
            p = args.lexicon_import_csv
            try:
                report = store.import_csv(csv_path=p, fallback_domain=active_domain)
            except FileNotFoundError as e:
                print(str(e), file=sys.stderr)
                return 2
            print(
                "词库导入完成："
                f"总计={report['total']} 导入={report['imported']} "
                f"跳过={report['skipped']} 失败={report['failed']} -> {cfg.lexicon.db_path}",
            )

        if args.lexicon_add and args.lexicon_add.strip():
            term = args.lexicon_add.strip()
            aliases = [x.strip() for x in args.lexicon_aliases.split(",") if x.strip()]
            store.upsert_term(term=term, aliases=aliases, domain=active_domain, weight=100)
            print(f"已写入术语：{term}（domain={active_domain}）")

        if args.lexicon_list:
            items = store.list_terms(domain=active_domain)
            print(f"词库：{cfg.lexicon.db_path} | domain={active_domain} | 术语数={len(items)}")
            for term, weight, alias_count in items:
                print(f"- {term} (weight={weight}, aliases={alias_count})")
        if args.lexicon_export_csv:
            out_path = args.lexicon_export_csv
            rows = store.export_rows(domain=active_domain)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["term", "aliases", "domain", "weight"])
                writer.writeheader()
                writer.writerows(rows)
            print(f"词库导出完成：{len(rows)} 条 -> {out_path}")
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
