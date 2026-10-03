# TermGuard

複数のターミナル / Claude Code セッションが同じ場所（`cwd`・ファイル・ポート等）で作業して衝突するのを防ぐ macOS 用の軽量な見張り番です。

## ざっくり何をするか

並行で複数の `claude` セッションを開いて、同じディレクトリ・同じファイル・同じポートをいじってしまう事故を防ぎます。

各セッションが `~/.termguard/sessions/<uuid>.json` に「自分は今ここで作業中」というカードを置き、他のセッションのカードと突き合わせて重複があれば **赤バナー + macOS 通知（osascript）** で警告します。

- 複数ターミナルの作業をセッションとして記録する
- 同じ `cwd` や同じリソースを別セッションが触ったら警告する
- macOS の通知センターにアラートを出す
- ターミナル上でも赤いバナーを出す

## サブコマンド

| コマンド | 役割 |
|---|---|
| `termguard run --resource cwd:. -- npm dev` | コマンドをラップして実行。子プロセスを動かしつつポーリング監視 |
| `termguard watch --resource file:src/app.py` | コマンドは動かさず、ターミナルだけ占有予約（`Ctrl+C` で離脱） |
| `termguard status` | 現在の全セッション一覧と衝突マトリクスを表示 |
| `termguard stop --session-id <id> / --all` | セッションカードを手動削除 |

## 使い方

### 監視しながらコマンドを実行

```bash
python3 termguard.py run --name api --resource cwd:. --resource port:3000 -- python3 app.py
```

### ターミナルを常駐監視

```bash
python3 termguard.py watch --name editor --resource cwd:.
```

### 状態確認

```bash
python3 termguard.py status
```

### 監視解除

```bash
python3 termguard.py stop --all
```

`stop` は TermGuard のセッション登録を消すだけで、実行中のプロセスは停止しません。

## リソース表記

- `cwd:/path/to/project`
- `file:/path/to/file`
- `port:3000`
- `branch:main`
- `raw:anything`

相対パスは自動で絶対パスに `resolve()` されます。プレフィックスのないものは `raw:` 扱いになります。

## 設計上の見どころ

1. **PID 死活で自動掃除** — `os.kill(pid, 0)` チェックでクラッシュ放置のゴミセッションが残らない
2. **リソースの正規化** — `cwd:./foo` のような相対パスを絶対パスに `resolve()`、未知プレフィックスは `raw:` フォールバック
3. **差分通知** — 直前と同じ衝突状態なら通知を抑制。連打されない
4. **状態ストアは JSON ファイルだけ** — DB なし。`TERMGUARD_HOME` 環境変数で保存先を切替可能
5. **macOS 通知のフォールバック** — `osascript` が無ければ `\a`（ベル）に降格

## 補足

- 通知は `osascript` を使います
- 追加で `terminal-notifier` があれば差し替え可能です
- macOS 専用設計（`os.uname()` と `osascript` 前提）
- まずは「軽さ優先」の最小版です


## 歴史的背景

[2026年5月8日の初期リリース](https://github.com/masa-san-jp/termguard/commit/f89a62f6d77349bec7e816f869bf154da315dad9) として、並行ターミナルの衝突を警告するCLIを追加しました。上記の軽量なJSON状態ストアと通知中心の設計は、この最小版の方針です。

## 展開・適用範囲

[termguard.py](termguard.py) の run / watch で作業資源を明示登録し、status で重複を確認する使い方から始めます。これは登録された資源の一致を警告する仕組みであり、ファイルロックやGitの競合解消を代行するものではありません。未登録の作業を網羅する保証もないため、重要な編集ではブランチや作業ディレクトリの分離を併用してください。通知の差し替えなどの補足案は、導入済み機能とは区別して検討します。
