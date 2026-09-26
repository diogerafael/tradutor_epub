# Tradutor de Livros (EPUB)

Tradutor de livros técnicos em EPUB do inglês para português do Brasil, usando a API do
DeepSeek. O HTML de cada capítulo é segmentado, as tags inline são mascaradas, o texto é
traduzido em blocos (batches) e o EPUB é reescrito com as imagens originais preservadas.

## Requisitos

- Python 3.9+ (testado em 3.12)
- Uma chave da API DeepSeek
- Nenhuma dependência externa — apenas a biblioteca padrão

## Instalação

```bash
export DEEPSEEK_API_KEY="sk-..."
cp config.example.json config.json
```

`config.json` é ignorado pelo git. Se o arquivo não existir, o programa usa os padrões
internos (`epub_translator/config.py`) e lê a chave apenas do ambiente.

## Uso

Planejar sem chamar a API e sem gastar tokens:

```bash
python translator.py extract -i "livro.epub"
```

Traduzir (gera `livro.pt-BR.epub` ao lado do original):

```bash
python translator.py translate -i "livro.epub"
```

Traduzir apenas os 20 primeiros arquivos de conteúdo, em outro destino:

```bash
python translator.py translate -i "livro.epub" -o "saida/livro.pt-BR.epub" --limit 20
```

### Opções

| Opção | Descrição |
| --- | --- |
| `-i`, `--input` | Caminho do EPUB de origem (obrigatório) |
| `-o`, `--output` | EPUB de saída (padrão: `<nome>.pt-BR.epub`) |
| `-c`, `--config` | Arquivo de configuração (padrão: `config.json`) |
| `--limit N` | Processa apenas os N primeiros arquivos de conteúdo |
| `--report ARQ` | Salva o relatório de planejamento em JSON |
| `--dry-run` | Não chama a API e não grava o EPUB |

## Configuração

Tudo em `config.json` (valores padrão em `config.example.json`):

| Chave | Padrão | Descrição |
| --- | --- | --- |
| `base_url` | `https://api.deepseek.com` | Base da API |
| `endpoint` | `""` | Sobrescreve a URL (`/chat/completions` por padrão) |
| `model` | `deepseek-chat` | Modelo usado |
| `api_key_env` | `DEEPSEEK_API_KEY` | Variável de ambiente com a chave |
| `api_key` | `""` | Chave inline (evite; use a variável de ambiente) |
| `MAX_CHUNK_TOKENS` | `3000` | Tokens por chunk enviado à API |
| `max_output_tokens` | `4096` | `max_tokens` da requisição |
| `MAX_RETRIES` | `5` | Tentativas por requisição |
| `timeout_seconds` | `120` | Timeout HTTP |
| `temperature` | `0.2` | Temperatura de amostragem |
| `source_lang` | `English` | Idioma de origem |
| `target_lang` | `Brazilian Portuguese` | Idioma de destino |
| `glossary_file` | `glossary.txt` | Glossário de terminologia |
| `prompt_file` | `prompts/translation_prompt.txt` | Prompt de sistema |
| `cache_dir` | `.cache` | Diretório do cache de chunks |

Caminhos relativos no `config.json` são resolvidos a partir do diretório do próprio
config, não do diretório atual.

## Como funciona

1. **Planejamento** — `epub_io` lê `META-INF/container.xml`, o OPF e a `spine` para localizar
   os arquivos XHTML de conteúdo.
2. **Segmentação** — `html.py` faz o tokenize do HTML e separa blocos (parágrafos, títulos,
   itens de lista) de segmentos traduzíveis. Tags inline viram placeholders `[[0]]`, `[[1]]`…
   que o modelo recebe intactos. Conteúdo de `<pre>`, `<code>`, `<script>`, `<style>`, `<svg>`
   e `<math>` nunca é traduzido.
3. **Chunking** — segmentos maiores que `MAX_CHUNK_TOKENS` são divididos por fronteira de
   sentença (com corte duro por espaço como fallback) e agrupados em chunks do mesmo tamanho.
4. **Tradução** — cada chunk vai em uma requisição com `response_format: json_object`; a
   resposta é validada (JSON, contagem e placeholders). Se algo falha, há retry com backoff
   exponencial e, em seguida, fallback item a item. Placeholders perdidos são reenviados ao
   modelo como hint na próxima tentativa.
5. **Reconstrução** — os placeholders viram as tags originais, o HTML é remontado e gravado
   num novo zip com `mimetype` sem compressão (requisito do formato EPUB).
6. **Validação** — os hashes SHA-256 de todas as imagens são comparados entre a entrada e a
   saída; qualquer divergência aborta com erro.

Falhas em um chunk não interrompem o livro: o texto original é mantido naquele trecho e o
motivo é listado em `AVISO:` no stderr e em `stats["errors"]`.

## Consistência terminológica

`glossary.txt` usa `termo em inglês = termo em português`, com `#` para comentários. Ele é
injetado no prompt de sistema a cada requisição:

```
bounded context = bounded context
aggregate = agregado
value object = objeto de valor
```

## Cache

Cada chunk traduzido é gravado em `.cache/<arquivo>_chunk_NNN.json` com um hash do conteúdo
mascarado. Reprocessar o mesmo livro reaproveita o cache e não chama a API de novo; se o
texto de origem mudar, o hash diverge e o chunk é retraduzido. O cache é seguro para apagar a
qualquer momento (ao custo de retraduzir) e nunca é versionado.

## Estrutura

```
translator.py                    # CLI (extract / translate)
epub_translator/
  config.py                      # carregamento e defaults do config
  epub_io.py                     # container.xml, OPF, spine, escrita e validação
  html.py                        # tokenize, mascaramento, segmentos, chunking
  api.py                         # cliente DeepSeek, retries, validação de placeholders
  prompt.py                      # montagem dos prompts (system / batch / single)
  glossary.py                    # leitura e renderização do glossário
  cache.py                       # cache de chunks em disco
  pipeline.py                    # orquestração extract / translate
prompts/translation_prompt.txt   # prompt de sistema
glossary.txt                     # glossário padrão
```

## Uso responsável

Traduzir obras sem autorização pode violar direitos autorais. Use apenas material que você
tenha direito de traduzir.
