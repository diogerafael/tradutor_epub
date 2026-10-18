import argparse
import json
import os
import zipfile

from epub_translator import epub_io, pipeline
from epub_translator.config import load_config


def resolve_output(input_path, output):
    if output:
        return output
    stem = os.path.splitext(input_path)[0]
    return f"{stem}.pt-BR.epub"


def main():
    parser = argparse.ArgumentParser(
        description="Tradutor de EPUB (ingles -> portugues do Brasil) via DeepSeek"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("extract", "translate"):
        sp = sub.add_parser(name, help=f"{name} conteudo do EPUB")
        sp.add_argument("--input", "-i", required=True, help="caminho do EPUB de origem")
        sp.add_argument("--output", "-o", help="caminho do EPUB de saida (default: <nome>.pt-BR.epub)")
        sp.add_argument("--config", "-c", default="config.json", help="caminho do config.json (default: config.json)")
        sp.add_argument("--limit", type=int, help="processa apenas os N primeiros arquivos")
        sp.add_argument("--report", help="salva relatorio JSON neste caminho")
        sp.add_argument("--dry-run", action="store_true",
                        help="nao chama a API e nao grava EPUB (so planeja)")
        if name == "translate":
            sp.add_argument("--workers", "-j", type=int, default=1,
                            help="chunks traduzidos em paralelo dentro do mesmo livro (default: 1)")

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "translate" and args.workers < 1:
        print("ERRO: --workers deve ser >= 1")
        return 1

    if not os.path.exists(args.input):
        print(f"ERRO: arquivo nao encontrado: {args.input}")
        return 1

    with zipfile.ZipFile(args.input) as zf:
        content_files = epub_io.get_content_files(zf)
        if args.limit:
            content_files = content_files[: args.limit]

        report = pipeline.extract_report(zf, content_files, cfg)

        if args.cmd == "extract":
            pipeline.print_report(report)
            if args.report:
                with open(args.report, "w", encoding="utf-8") as f:
                    json.dump(report, f, ensure_ascii=False, indent=2)
            return 0

        out_path = None if args.dry_run else resolve_output(args.input, args.output)
        if not args.dry_run and not cfg.api_key:
            print("ERRO: defina a variavel de ambiente DEEPSEEK_API_KEY para traduzir.")
            return 1

        pipeline.print_report(report)
        print(f"\nIniciando traducao (dry_run={args.dry_run})...")
        _, stats = pipeline.translate(zf, content_files, cfg,
                                      out_path=out_path, dry_run=args.dry_run,
                                      workers=args.workers)
        print(f"Chunks em cache: {stats['chunks_cached']}")
        print(f"Chunks traduzidos: {stats['chunks_translated']}")
        if stats.get("chunks_failed"):
            print(f"Chunks com falha (mantidos no original): {stats['chunks_failed']}")
        if out_path:
            print(f"EPUB gerado: {out_path}")
            print(f"Imagens validadas: {stats.get('images_validated', 0)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
