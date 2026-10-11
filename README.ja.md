<p><img src="docs/images/logo/lockup-dark.svg" alt="vtskit" height="56"></p>

# vtskit

**計画中：** Python だけで DVD-Video をオーサリングするライブラリです。IFO ファイル、VOB のナビゲーションパック、メニュー、DVD のナビゲーション命令まで、VIDEO_TS を自前で書き出す予定です。このリポジトリには今は計画だけがあり、コードはまだありません。

## なぜ作るか

今、オープンソースで DVD をオーサリングするには dvdauthor を使うしかありません。dvdauthor は古く、マルチアングルのように、そもそも作れないものもあります。ffmpeg は DVD 用の映像ファイルを書けますが、中のナビゲーションパックは空のままです。

vtskit は、他のツールが土台にできる、完成したテスト済みの書き出しライブラリを目指します。Blu-ray における [bdmvkit](https://github.com/kajisho5/bdmvkit) と同じ位置づけです。

## 計画

[oncemore](https://github.com/kajisho5/oncemore) から段階的に作り、各段階を同じ再生テストで確かめます。詳しい計画は [oncemore#75](https://github.com/kajisho5/oncemore/issues/75) にあります。

1. **タイトルセットを書く処理**：VOB の配置（アングルブロックのインターリーブを含む）、ナビゲーションパック（PCI / DSI）、VTS の IFO を書きます。最初は、dvdauthor では作れないマルチアングルのタイトルに使います。
2. **通常のタイトル**も同じ処理で書き、dvdauthor の出力と比べます。
3. **メニュー、VMG、DVD のナビゲーション命令の変換器**を作ります。
4. **oncemore から dvdauthor を外します。**

**独自に実装します：** DVD-Video 形式の公開資料をもとに書き、dvdauthor・libdvdread・libdvdnav のソースは参考にしません。libdvdnav は、書き出したディスクを再生して確かめるテストにだけ使います。

## 支援する

vtskit は無料で、これからも無料です。役に立ったら [GitHub Sponsors](https://github.com/sponsors/kajisho5)（このリポジトリ上部の **Sponsor** ボタン）から開発を支援できます。不具合の報告も同じくらい助かります：[Issues](https://github.com/kajisho5/vtskit/issues)。

ロゴ・色・文章の決まりは [docs/DESIGN.md](docs/DESIGN.md)（英語）にあります。

## ライセンス

GNU Lesser General Public License バージョン 3 以降（[COPYING.LESSER](COPYING.LESSER) と [COPYING](COPYING) を参照）。
