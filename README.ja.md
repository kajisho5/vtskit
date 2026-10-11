<p><img src="docs/images/logo/lockup-dark.svg" alt="vtskit" height="56"></p>

# vtskit

**開発中：** Python だけで DVD-Video をオーサリングし、dvdauthor を置き換えるライブラリです。IFO ファイル、VOB のナビゲーションパック、メニュー、DVD のナビゲーション命令まで、VIDEO_TS を自前で書き出す予定です。最初の部分（ナビゲーションパック）はできていて、dvdauthor の出力とバイト単位で一致します。次は IFO ファイルです（[いまあるもの](#いまあるもの)）。

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

**入力：** oncemore のエンコーダが作る MPEG-2 プログラムストリーム（ffmpeg の `-f dvd`。2048 バイトのパックで、GOP ごとにナビゲーションパックがあり、その PCI と DSI は空）。字幕がある場合は spumux で加えたもの。vtskit はナビゲーションパックの中身を埋め、ほかのパックの位置は変えません。ただし詰め物だけのパック（spumux が残すことがある）は、dvdauthor と同じく取り除きます。

**独自に実装します：** DVD-Video の規格書そのものは公開されていません（DVD FLLC が秘密保持契約のもとでライセンスしていました）。vtskit は形式についての公開資料をもとに書きます。

* [DVD-Video information](https://dvdnav.mplayerhq.hu/dvdinfo/)（IFO、PGC、PCI / DSI、命令セット。作者は規格書を見ずに書いたと明記）
* [Inside DVD-Video](https://en.wikibooks.org/wiki/Inside_DVD-Video)（Wikibooks、CC BY-SA）

資料に書かれていないことは、dvdauthor が書き出したバイト列を観察して決めます。dvdauthor・libdvdread・libdvdnav のソースは読みません。libdvdnav は、書き出したディスクを再生して確かめるテストにだけ、公開 API（`dvdnav.h` の関数宣言）を通して使います。

## いまあるもの

計画のステップ1と3が入り、タイトルとメニューのあるディスクを書けます。

* `vtskit.titleset`：タイトルセットを書きます。タイトルの VOB（タイトルごとに1本、1 GiB 未満のファイルに分割）にナビゲーションパックを埋め、VTS_nn_0.IFO と BUP を書きます。IFO には、ストリームの属性、各タイトルのチャプター、タイトルごとのプログラムチェーン（チャプターごとのプログラムとセル、バイト列で与える pre・post・セル命令）、タイムマップ、セルと VOBU のアドレス表が入ります。チャプターは dvdauthor と同じく、指定時刻に最も近い VOBU の先頭から始まります。メニュー（`vtskit.menu`：Menu のストリーム、矩形・矢印キーの行き先・命令を持つ Button、セル、応答するリモコンキー）があれば、メニュー VOB も書きます。ボタンの光る部分はサブピクチャ（`vtskit.spu`）、ボタン情報は全ナビゲーションパックのハイライト情報に入り、IFO にメニューの表が加わります。アングルはまだありません。
* `vtskit.vmg`：ビデオマネージャ、VIDEO_TS.IFO と BUP を書きます。First Play の命令、タイトル表、タイトルセットの属性の写しが入り、タイトルメニューがあれば VIDEO_TS.VOB も書きます。`vtskit.titleset` と合わせて VIDEO_TS 全体を書けます。
* `vtskit.vm`：ナビゲーション命令（dvdinfo の vmi.html）を符号化します。ジャンプ・コール・リンク、システムレジスタと汎用レジスタ、そして `If` ブロックを dvdauthor が自分の言語を変換するのと同じ形で Goto 行に展開します。
* `vtskit.nav`：VOB のナビゲーションパックを埋めます。PCI（時刻）と DSI（VOBU と参照フレームの終端、0.5 秒から 120 秒先の VOBU の位置、音声と字幕の位置）。`vtskit.ps` がプログラムストリームを読みます。
* `vtskit.ifo`：IFO ファイルから、各タイトルの各チャプターがディスクのどのセクターから始まるかを読みます。
* `tests/dvdnav_sim.py`：libdvdnav を ctypes で呼んでディスクを再生します（タイトル、チャプター、メニューとボタン、音声・字幕トラック、アングル）。

テストは同じタイトルを dvdauthor と vtskit に渡し、IFO と VOB が全バイト一致することを確かめます。NTSC と PAL、4:3 と 16:9、VOBU 先頭に乗るチャプターと乗らないチャプター、1 タイトルセットに 2 タイトル、言語付きの AC-3・LPCM・MPEG 音声、B フレーム、短い GOP、40 秒のタイトル、字幕トラック、2 つのタイトルセット。命令はどの形も、同じ文を dvdauthor が変換したバイト列と一致します。メニューは、全ナビゲーションパックのハイライト情報と IFO のメニュー表が dvdauthor（ボタンは spumux 製）と一致します。vtskit だけで書いた VIDEO_TS は、libdvdnav でタイトルとチャプターが順に再生され、命令でタイトル間を移動し、メニューのボタンは指定どおりの先へ進みます（`python -m unittest discover -s tests -t .`。必要な道具が無いテストはスキップします）。次はアングルです。

`vtskit.ifo` と `tests/dvdnav_sim.py` は [oncemore](https://github.com/kajisho5/oncemore) のもの（`layerbreak.py` と `tests/dvdnav_sim.py`。oncemore では GPL-3.0）で、作者がここでは LGPL-3.0-or-later で再ライセンスしています。

## 支援する

vtskit は無料で、これからも無料です。役に立ったら [GitHub Sponsors](https://github.com/sponsors/kajisho5)（このリポジトリ上部の **Sponsor** ボタン）から開発を支援できます。不具合の報告も同じくらい助かります：[Issues](https://github.com/kajisho5/vtskit/issues)。

ロゴ・色・文章の決まりは [docs/DESIGN.md](docs/DESIGN.md)（英語）にあります。

## ライセンス

GNU Lesser General Public License バージョン 3 以降（[COPYING.LESSER](COPYING.LESSER) と [COPYING](COPYING) を参照）。
