<p><img src="docs/images/logo/lockup-dark.svg" alt="vtskit" height="56"></p>

# vtskit

**開発中：** Python だけで DVD-Video をオーサリングし、dvdauthor を置き換えるライブラリです。IFO ファイル、VOB のナビゲーションパック、メニュー、DVD のナビゲーション命令まで、VIDEO_TS を自前で書き出す予定です。書き出す処理そのものはまだありません。いまあるのは計画と、書き出しを確かめるための道具です（[いまあるもの](#いまあるもの)）。

## なぜ作るか

今、オープンソースで DVD をオーサリングするには dvdauthor を使うしかありません。dvdauthor は古く、マルチアングルのように、そもそも作れないものもあります。ffmpeg は DVD 用の映像ファイルを書けますが、中のナビゲーションパックは空のままです。

vtskit は、他のツールが土台にできる、完成したテスト済みの書き出しライブラリを目指します。Blu-ray における [bdmvkit](https://github.com/kajisho5/bdmvkit) と同じ位置づけです。

## 計画

[oncemore](https://github.com/kajisho5/oncemore) から段階的に作り、各段階を同じ再生テストで確かめます。詳しい計画は [oncemore#75](https://github.com/kajisho5/oncemore/issues/75) にあります。

1. **タイトルセットを書く処理（まず通常のタイトル）**：VOB（1 GiB ごとに分割）、そのナビゲーションパック（PCI / DSI）、VTS の IFO と BUP を書き、同じタイトルを dvdauthor が書いたものと比べます。oncemore のタイトルが持つナビゲーション命令（pre・post・セル命令。言語ではなく数値で受け取る）を書く小さなエンコーダも含め、実際のディスクで dvdauthor のタイトルセットと置き換えられるようにします。
2. **マルチアングルのタイトル**（dvdauthor では作れない）：アングルブロックのインターリーブと、それに合わせた VMG のタイトル表・タイトルセット属性。
3. **メニュー、VMG、DVD のナビゲーション命令の変換器**を作ります。
4. **oncemore から dvdauthor を外します。**

通常のタイトルを先にするのは、dvdauthor の出力と項目ごとに比べられるからです。周りのタイトルセットが正しくないうちは、アングルブロックを確かめられません。

**入力：** oncemore のエンコーダが作る MPEG-2 プログラムストリーム（ffmpeg の `-f dvd`。2048 バイトのパックで、VOBU（oncemore のストリームでは 1 GOP）の先頭ごとにナビゲーションパックがあり、その PCI と DSI は空）。vtskit はナビゲーションパックの中身を埋め、パックの位置は変えません。

**独自に実装します：** DVD-Video の規格書そのものは公開されていません（DVD FLLC が秘密保持契約のもとでライセンスしていました）。vtskit は形式についての公開資料をもとに書きます。

* [DVD-Video information](https://dvdnav.mplayerhq.hu/dvdinfo/)（IFO、PGC、PCI / DSI、命令セット。作者は規格書を見ずに書いたと明記）
* [Inside DVD-Video](https://en.wikibooks.org/wiki/Inside_DVD-Video)（Wikibooks、CC BY-SA）

資料に書かれていないことは、dvdauthor が書き出したバイト列を観察して決めます。dvdauthor・libdvdread・libdvdnav のソースは読みません。libdvdnav は、書き出したディスクを再生して確かめるテストにだけ、公開 API（`dvdnav.h` の関数宣言）を通して使います。

## いまあるもの

書き出す処理の最初の部品と、それを確かめる道具です。

* `vtskit.vob`：タイトルのプログラムストリームのナビゲーションパックを埋めます。`scan` がストリームの VOBU を読み、`cells_for` が dvdauthor と同じ位置にチャプターを置き、`write` が全ての PCI と DSI（時刻、参照フレームの終端、探索テーブル、音声の同期）を埋めてストリームを書き写し、IFO に書くセルの情報を返します。アングル、ボタン、字幕はまだ書きません。`vtskit.nav` は2つのパケットのレイアウト、`vtskit.ps` はストリームの読み取りです。
* `vtskit.ifo`：IFO ファイルから、各タイトルの各チャプターがディスクのどのセクターから始まるかを読みます。
* `tests/dvdnav_sim.py`：libdvdnav を ctypes で呼んでディスクを再生します（タイトル、チャプター、メニューとボタン、音声・字幕トラック、アングル）。

テストは ffmpeg と dvdauthor でディスクを作り、それと比べます。vtskit が書いた VOB は、同じストリームから dvdauthor が書いた VOB と全バイト比較します（NTSC と PAL、チャプター、B フレーム、1 VOBU に複数 GOP、AC-3・MPEG・LPCM 音声）。VOB を vtskit のものに置き換えたディスクを libdvdnav で再生します（`python -m unittest discover -s tests -t .`。必要な道具が無いテストはスキップします）。

どちらも [oncemore](https://github.com/kajisho5/oncemore) のもの（`layerbreak.py` と `tests/dvdnav_sim.py`。oncemore では GPL-3.0）で、作者がここでは LGPL-3.0-or-later で再ライセンスしています。

## 支援する

vtskit は無料で、これからも無料です。役に立ったら [GitHub Sponsors](https://github.com/sponsors/kajisho5)（このリポジトリ上部の **Sponsor** ボタン）から開発を支援できます。不具合の報告も同じくらい助かります：[Issues](https://github.com/kajisho5/vtskit/issues)。

ロゴ・色・文章の決まりは [docs/DESIGN.md](docs/DESIGN.md)（英語）にあります。

## ライセンス

GNU Lesser General Public License バージョン 3 以降（[COPYING.LESSER](COPYING.LESSER) と [COPYING](COPYING) を参照）。
