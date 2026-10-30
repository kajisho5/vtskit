<p><img src="docs/images/logo/lockup-dark.svg" alt="vtskit" height="56"></p>

# vtskit

Python だけで DVD-Video をオーサリングし、dvdauthor の代わりになるライブラリです。IFO ファイル、VOB のナビゲーションパック、メニュー、字幕、アングルブロック、DVD のナビゲーション命令（dvdauthor の命令言語からの変換も）まで、VIDEO_TS を自前で書き出します。dvdauthor が同じものを書く範囲では、バイト列は dvdauthor と一致します（[いまあるもの](#いまあるもの)）。[oncemore](https://github.com/kajisho5/oncemore) は [oncemore#90](https://github.com/kajisho5/oncemore/pull/90) からこれでディスクを書いています（アングルは [#91](https://github.com/kajisho5/oncemore/pull/91) から）。

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

## インストール

vtskit は標準ライブラリだけの Python パッケージです（3.11 以降）。次で入ります。

```
pip install vtskit
```

リリースはバージョン番号で決まります。`pyproject.toml`（と `vtskit/__init__.py`）のバージョンがまだタグの無い番号で `main` に入ると、`.github/workflows/release.yml` がテスト・ビルド・タグ付きの GitHub リリース・PyPI 公開まで行います。バージョンを上げた PR をマージするだけで済みます（手で `v*` タグを打っても同じように動きます）。PyPI 側にはこのリポジトリとワークフローが trusted publisher として登録してあります。oncemore は vtskit をインストールせず、リリースの写しを `oncemore/_vendor/vtskit` として持ち、`tools/vendor.py vtskit --pypi <版>` で PyPI から取り込みます（新しいリリースが出た日には、oncemore 側でそのプルリクエストが自動で作られます）。

## いまあるもの

計画の 4 ステップがすべて入り、タイトル、メニュー、字幕、アングルのあるディスクを書け、dvdauthor の命令言語も変換でき、oncemore はこれでディスクを書いています。

* `vtskit.titleset`：タイトルセットを書きます。タイトルの VOB（タイトルごとに1本、1 GiB 未満のファイルに分割）にナビゲーションパックを埋め、VTS_nn_0.IFO と BUP を書きます。IFO には、ストリームの属性、各タイトルのチャプター、タイトルごとのプログラムチェーン（チャプターごとのプログラムとセル、バイト列で与える pre・post・セル命令）、タイムマップ、セルと VOBU のアドレス表が入ります。チャプターは dvdauthor と同じく、指定時刻に最も近い VOBU の先頭から始まります。タイトルのストリームはメモリマップで読み、ナビゲーションパックだけを別に持ってブロック単位で書き出すので、数 GB のタイトルをメモリに抱え込みません。メニュー（`vtskit.menu`：Menu のストリーム、矩形・矢印キーの行き先・命令を持つ Button、セル、応答するリモコンキー）があれば、メニュー VOB も書きます。ボタンの光る部分はサブピクチャ（`vtskit.spu`。選ばれるまで見えないか、通常色で常に表示）、ボタン情報は全ナビゲーションパックのハイライト情報に入り、IFO にメニューの表が加わります。ストリームの無い Menu は命令だけのプログラムチェーンになります（dvdauthor の `<vob>` の無い `<pgc>`。飛ぶだけのタイトルメニュー、経路だけのルートメニュー）。メニューは指定したリモコンキー（root、audio、subpicture、angle、ptt）に応答し、dvdauthor と同じく、root メニューの無いドメインでは最初のメニューが root になり、チャプターメニューの無いタイトルセットには最初のメニューへ飛ぶだけのものが足されます。タイトルには前後・上位のプログラムチェーン（`next_pgc`・`prev_pgc`・`up_pgc`）を指定でき、libdvdnav では post 命令の尽きたタイトルが次のプログラムチェーンへ続きます（dvdauthor はこれらを書きません）。タイトルは複数のパート（ストリームと、2〜9 本のストリームからなる `Angles` ブロック）の列にもできます。アングルブロックはディスク上でインターリーブされ、セルはアングルブロックとして印が付きます。シームレスのブロック（既定）はインターリーブユニットの終わりで切り替わり、ナビゲーションパックは各ユニットから全アングルの次ユニットを指し（SML_AGLI）、セルには「シームレスアングル」の印が付きます。途切れて切り替わるブロック（`seamless=False`）は、代わりに全アングルの同じ時刻の VOBU を指します（NSML_AGLI）。プレーヤーは NSML_AGLI があればそちらを使うので、埋めるのはどちらか一方だけです。インターリーブユニットの先頭がオープン GOP（前の GOP を参照する B ピクチャ。切り替え後は別アングルの GOP になる）のストリームは拒否します。タイトルには、その一部の区間に重ねるボタン（`vtskit.menu.TitleButtons`：開始・終了時刻、ボタン、ボタンを描くパレット番号）を付けられます。ボタンの光る部分は `vtskit.subtitles` で加える強制表示のサブピクチャで、ハイライト情報はその区間に再生される VOBU にだけ入り（2 つの区間が 1 つの VOBU の中で接するときは、その VOBU を長く占める方）、区間の開始・終了がハイライトの開始時刻・終了時刻・選択の終了時刻（dvdinfo の hli_s_ptm、hli_e_ptm、btn_sl_e_ptm）になります。dvdauthor はタイトルのボタンをタイトル全体に付けます（全 VOBU、終わり無し）。終了時刻を指定しないボタンはそれと同じものを書きます。dvdauthor と同じく、ボタンのあるタイトルは最初のボタンを選んだ状態で始まります（タイトル自身の pre 命令の前に SetHL_BTNN 1）。
* `vtskit.subtitles`：spumux のように、タイトルのプログラムストリームに字幕を加えます。各キューは 4 色までの絵を画面上の位置に置き、一定時間表示するもので、表示時刻の少し前にサブピクチャのパックとして入ります。
* `vtskit.lang`：dvdauthor の命令言語（`jump title 2;`、`if (g5 != 0) { button = g5; }`、`call menu entry root;`）を、実行されるドメインに応じて、dvdauthor が書くのと同じ命令に変換します。oncemore のようにこの言語をすでに使うツールは、その文面をそのまま渡せます。複数命令のボタンは、メニューのボタンでもタイトルのボタンでも、プログラムチェーンの post 命令から振り分けて実行し、別タイトルセットのチャプターへのジャンプは dvdauthor の「jumppad」と同じく中継のプログラムチェーンを通ります。
* `vtskit.vmg`：ビデオマネージャ、VIDEO_TS.IFO と BUP を書きます。First Play の命令、タイトル表、タイトルセットの属性の写しが入り、タイトルメニューがあれば VIDEO_TS.VOB も書きます。`vtskit.titleset` と合わせて VIDEO_TS 全体を書けます。
* `vtskit.vm`：ナビゲーション命令（dvdinfo の vmi.html）を符号化します。ジャンプ・コール・リンク、システムレジスタと汎用レジスタ、そして `If` ブロックを dvdauthor が自分の言語を変換するのと同じ形で Goto 行に展開します。
* `vtskit.nav`：VOB のナビゲーションパックを埋めます。PCI（時刻）と DSI（VOBU と参照フレームの終端、0.5 秒から 120 秒先の VOBU の位置、音声と字幕の位置）。`vtskit.ps` がプログラムストリームを読みます。
* `vtskit.ifo`：IFO ファイルから、各タイトルの各チャプターがディスクのどのセクターから始まるかを読みます。
* `tests/dvdnav_sim.py`：libdvdnav を ctypes で呼んでディスクを再生します（タイトル、チャプター、メニューとボタン、音声・字幕トラック、アングル）。

テストは同じタイトルを dvdauthor と vtskit に渡し、IFO と VOB が全バイト一致することを確かめます。NTSC と PAL、4:3 と 16:9、VOBU 先頭に乗るチャプターと乗らないチャプター、1 タイトルセットに 2 タイトル、言語付きの AC-3・LPCM・MPEG 音声、B フレーム、短い GOP、40 秒のタイトル、字幕トラック、2 つと 3 つのタイトルセット（3 つでは VMG の属性表が 2 セクタになる）。命令はどの形も、同じ文を dvdauthor が変換したバイト列と一致し、この言語で書いたディスク（命令だけのメニュー、複数命令のボタン、セル命令、2 つのタイトルセット）は IFO が dvdauthor と全バイト一致します。メニューは、全ナビゲーションパックのハイライト情報と IFO のメニュー表が dvdauthor（ボタンは spumux 製）と一致し、通常色のあるメニューのサブピクチャは spumux のユニットとバイト単位で一致します。vtskit が多重化した字幕は spumux のユニットと同じバイト列が同じ位置に入り、そのストリームから書いたタイトル（4:3 と、パンスキャン・レターボックスの有無それぞれの 16:9）は dvdauthor と一致します。タイトル全体に付けたボタン（うち 1 つは複数命令）は、VOB と IFO が dvdauthor と全バイト一致します（パレットを除く）。vtskit だけで書いた VIDEO_TS は、libdvdnav でタイトルとチャプターが順に再生され、命令でタイトル間を移動し、メニューのボタンは指定どおりの先へ進み、別タイトルセットのチャプターへのジャンプも着地します。アングルブロックのあるタイトルは libdvdnav で再生・切替でき、ナビゲーションパックの全ポインタが指すユニットに実際に着地し、切り替えをはさんで libdvdnav がプレーヤーに渡す映像を ffmpeg でデコードすると、どの絵も一度ずつ順に並び、ユニットの終わりから新しいアングルになります（dvdauthor はアングルを書けないので、据え置きプレーヤーでの確認が残っています）。タイトルの一部の区間に付けたボタンは、libdvdnav でその区間の VOBU でだけ選べ、命令どおりに動きます（`python -m unittest discover -s tests -t .`。必要な道具が無いテストはスキップします）。oncemore の vtskit への切り替え（ステップ 4）は [oncemore#90](https://github.com/kajisho5/oncemore/pull/90) と [#91](https://github.com/kajisho5/oncemore/pull/91) でマージ済みで、`vtskit/` の写しを `oncemore/_vendor/vtskit` として持ちます。

`vtskit.ifo` と `tests/dvdnav_sim.py` は [oncemore](https://github.com/kajisho5/oncemore) のもの（`layerbreak.py` と `tests/dvdnav_sim.py`。oncemore では GPL-3.0）で、作者がここでは LGPL-3.0-or-later で再ライセンスしています。

`python -m vtskit contract --json` で機械可読な契約（AI Video Production OS
レジストリの形）を表示します。パッケージ唯一のコマンドです。

## 支援する

vtskit は無料で、これからも無料です。役に立ったら [GitHub Sponsors](https://github.com/sponsors/kajisho5)（このリポジトリ上部の **Sponsor** ボタン）から開発を支援できます。不具合の報告も同じくらい助かります：[Issues](https://github.com/kajisho5/vtskit/issues)。

ロゴ・色・文章の決まりは [docs/DESIGN.md](docs/DESIGN.md)（英語）にあります。

## ライセンス

GNU Lesser General Public License バージョン 3 以降（[COPYING.LESSER](COPYING.LESSER) と [COPYING](COPYING) を参照）。
