// 保有銘柄。保有している銘柄の状態を見る。登録・編集・売却もこの画面で行う。
// 同じ銘柄を2回以上に分けて買った場合は「合計の行」にまとめ、「▶」で購入ごとの行を開く
import { Fragment, useState } from "react";

import type { HoldingGroup, HoldingsResponse } from "../api/types";
import { useApi } from "../api/useApi";
import ErrorMessage from "../components/ErrorMessage";
import { EditDialog, type LotTarget, RegisterDialog, SellDialog } from "../components/HoldingDialogs";
import NotesText from "../components/NotesText";
import StockName from "../components/StockName";
import Tabs from "../components/Tabs";
import { useSettings } from "../context/contexts";
import {
  displayCode,
  formatDateWithWeekday,
  formatInteger,
  formatPercent,
  formatPrice,
  formatSignedNumber,
  formatSignedPercent,
  signClass,
} from "../lib/format";
import { judgmentClass } from "../lib/labels";

type Dialog = { kind: "register" } | { kind: "edit"; target: LotTarget } | { kind: "sell"; target: LotTarget };

function HoldingTable({
  groups,
  horizons,
  main,
  onDialog,
}: {
  groups: HoldingGroup[];
  horizons: number[];
  main: number;
  onDialog: (d: Dialog) => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const predCols = horizons.length * 3;
  const buttons = (g: HoldingGroup, index: number) => {
    const lot = g.lots[index];
    if (!lot) return null;
    const target: LotTarget = { code: g.code, name: g.name, lot, index: g.lots.length > 1 ? index + 1 : null };
    return (
      <>
        <button type="button" className="btn sm sub" onClick={() => onDialog({ kind: "edit", target })}>
          編集
        </button>{" "}
        <button type="button" className="btn sm sub" onClick={() => onDialog({ kind: "sell", target })}>
          売却
        </button>
      </>
    );
  };
  return (
    <div className="wrap">
      <table>
        <thead>
          <tr>
            {["コード"].map((h) => (
              <th key={h} rowSpan={2}>
                {h}
              </th>
            ))}
            <th rowSpan={2} className="fix">
              銘柄名
            </th>
            {["市場", "買値", "買い株数", "購入日", "現在値", "損益(%)", "損益(円)"].map((h) => (
              <th key={h} rowSpan={2}>
                {h}
              </th>
            ))}
            {horizons.map((h) => (
              <th key={h} colSpan={3} className={h === main ? "main" : undefined}>
                {h}日後
              </th>
            ))}
            <th rowSpan={2}>判定</th>
            <th rowSpan={2}>備考</th>
            <th rowSpan={2}>操作</th>
          </tr>
          <tr>
            {horizons.flatMap((h) =>
              ["市場比", "上昇確率", "予測株価"].map((n) => (
                <th key={`${h}-${n}`} className={h === main ? "main" : undefined}>
                  {n}
                </th>
              )),
            )}
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => {
            const many = g.lots.length > 1;
            const isOpen = open[g.code] ?? false;
            return (
              <Fragment key={g.code}>
                <tr>
                  <td>
                    {many && (
                      <button
                        type="button"
                        className="tog"
                        aria-expanded={isOpen}
                        aria-label={`${g.name}の購入ごとの行を${isOpen ? "閉じる" : "開く"}`}
                        onClick={() => setOpen({ ...open, [g.code]: !isOpen })}
                      >
                        {isOpen ? "▼" : "▶"}
                      </button>
                    )}
                    {displayCode(g.code)}
                  </td>
                  <td className="fix">
                    <StockName code={g.code} name={g.name} />
                  </td>
                  <td>{g.market}</td>
                  <td className="n">
                    {formatPrice(g.avg_buy_price)}
                    {many && (
                      <>
                        <br />
                        <span className="sub">(平均)</span>
                      </>
                    )}
                  </td>
                  <td className="n">{formatInteger(g.quantity)}</td>
                  <td>{many ? `${g.lots.length}回に分けて購入` : g.lots[0]?.buy_date}</td>
                  <td className="n">{formatPrice(g.current)}</td>
                  <td className={`n ${signClass(g.pnl_rate)}`}>{formatSignedPercent(g.pnl_rate)}</td>
                  <td className={`n ${signClass(g.pnl_amount)}`}>{formatSignedNumber(g.pnl_amount)}</td>
                  {horizons.flatMap((h) => {
                    const v = g.horizons.find((x) => x.horizon === h);
                    const b = h === main ? "n b" : "n";
                    return [
                      <td key={`${h}-r`} className={b}>
                        {formatPercent(v?.rel_prob)}
                      </td>,
                      <td key={`${h}-u`} className={b}>
                        {formatPercent(v?.up_prob)}
                      </td>,
                      <td key={`${h}-p`} className={b}>
                        {formatPrice(v?.pred_price)}
                      </td>,
                    ];
                  })}
                  <td className={judgmentClass(g.judgment)}>{g.judgment}</td>
                  <td>
                    <NotesText notes={g.notes} />
                  </td>
                  <td className="c">{many ? "" : buttons(g, 0)}</td>
                </tr>
                {many &&
                  isOpen &&
                  g.lots.map((lot, i) => (
                    <tr key={lot.holding_id} className="lot">
                      <td />
                      <td className="fix">└ {i + 1}回目の購入</td>
                      <td />
                      <td className="n">{formatPrice(lot.buy_price)}</td>
                      <td className="n">{formatInteger(lot.quantity)}</td>
                      <td>{lot.buy_date}</td>
                      <td className="n">{formatPrice(g.current)}</td>
                      <td className={`n ${signClass(lot.pnl_rate)}`}>{formatSignedPercent(lot.pnl_rate)}</td>
                      <td className={`n ${signClass(lot.pnl_amount)}`}>{formatSignedNumber(lot.pnl_amount)}</td>
                      <td colSpan={predCols + 2} className="dash">
                        (判定は合計の行)
                      </td>
                      <td className="c">{buttons(g, i)}</td>
                    </tr>
                  ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SoldTable({ data }: { data: HoldingsResponse }) {
  return (
    <div className="wrap">
      <table>
        <thead>
          <tr>
            <th>コード</th>
            <th className="fix">銘柄名</th>
            <th>市場</th>
            <th>買値</th>
            <th>買い株数</th>
            <th>購入日</th>
            <th className="act">売値</th>
            <th className="act">売り株数</th>
            <th className="act">売却日</th>
            <th>現在値</th>
            <th>損益(%)</th>
            <th>損益(円)</th>
            <th>保有した日数</th>
          </tr>
        </thead>
        <tbody>
          {data.sold.length === 0 && (
            <tr>
              <td colSpan={13} className="c">
                売却済みの銘柄はありません
              </td>
            </tr>
          )}
          {data.sold.map((s) => (
            <tr key={s.holding_id}>
              <td>{displayCode(s.code)}</td>
              <td className="fix">
                <StockName code={s.code} name={s.name} />
              </td>
              <td>{s.market}</td>
              <td className="n">{formatPrice(s.buy_price)}</td>
              <td className="n">{formatInteger(s.quantity)}</td>
              <td>{s.buy_date}</td>
              <td className="n">{formatPrice(s.sell_price)}</td>
              <td className="n">{formatInteger(s.quantity)}</td>
              <td>{s.sell_date}</td>
              <td className="n">{formatPrice(s.current)}</td>
              <td className={`n ${signClass(s.pnl_rate)}`}>{formatSignedPercent(s.pnl_rate)}</td>
              <td className={`n ${signClass(s.pnl_amount)}`}>{formatSignedNumber(s.pnl_amount)}</td>
              <td className="n">{s.held_days}日</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function HoldingsPage() {
  const settings = useSettings();
  const res = useApi<HoldingsResponse>("/api/holdings");
  const [tab, setTab] = useState<"open" | "sold">("open");
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { main_horizon: main, predict_horizons: horizons } = settings.batch;
  const data = res.data;
  const saved = (text: string) => {
    setDialog(null);
    setMessage(text);
    res.reload();
  };

  return (
    <>
      <h1>保有銘柄</h1>
      <div className="cond">
        <Tabs
          label="保有中と売却済み"
          items={[
            { value: "open", label: `保有中(${data?.groups.length ?? 0})` },
            { value: "sold", label: `売却済み(${data?.sold_count ?? 0})` },
          ]}
          value={tab}
          onChange={setTab}
        />
        {data && tab === "open" && (
          <span className="sub">判定の日: {data.judge_date ? formatDateWithWeekday(data.judge_date) : "予測がまだありません"}</span>
        )}
        <button type="button" className="btn" style={{ marginLeft: "auto" }} onClick={() => setDialog({ kind: "register" })}>
          ＋ 銘柄を登録
        </button>
      </div>
      {message && (
        <div className="okbox" role="status">
          {message}
        </div>
      )}
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…</p>}
      {data && tab === "open" && (
        <>
          <div className="cards" style={{ marginBottom: 12 }}>
            <div className="card">
              <div className="lbl">買値の合計</div>
              <div className="val">{formatInteger(data.totals.cost)}</div>
            </div>
            <div className="card">
              <div className="lbl">今の評価額</div>
              <div className="val">{formatInteger(data.totals.value)}</div>
            </div>
            <div className="card">
              <div className="lbl">損益</div>
              <div className={`val ${signClass(data.totals.pnl_amount)}`}>
                {formatSignedNumber(data.totals.pnl_amount)}({formatSignedPercent(data.totals.pnl_rate)})
              </div>
            </div>
          </div>
          {data.groups.length === 0 ? (
            <p className="info">保有銘柄はありません。「＋ 銘柄を登録」から登録してください</p>
          ) : (
            <HoldingTable groups={data.groups} horizons={horizons} main={main} onDialog={setDialog} />
          )}
          <p className="info">
            損益は平均買値で計算する。判定と損切りラインは、平均買値で銘柄ごとに1つ出す(メールと同じ)。現在値は判定の日の終値
          </p>
        </>
      )}
      {data && tab === "sold" && (
        <>
          <div className="cards" style={{ marginBottom: 12 }}>
            <div className="card">
              <div className="lbl">買値の合計</div>
              <div className="val">{formatInteger(data.sold_buy_amount)}</div>
            </div>
            <div className="card">
              <div className="lbl">売値の合計</div>
              <div className="val">{formatInteger(data.sold_sell_amount)}</div>
            </div>
            <div className="card">
              <div className="lbl">売却した銘柄の損益の合計</div>
              <div className={`val ${signClass(data.sold_pnl_amount)}`}>
                {formatSignedNumber(data.sold_pnl_amount)}({formatSignedPercent(data.sold_pnl_rate)})
              </div>
            </div>
          </div>
          <SoldTable data={data} />
          <p className="info">損益は売値で計算する(損益(円)=(売値 − 買値)× 売り株数)。現在値は、売った後の値動きを見るために出す</p>
        </>
      )}
      {dialog?.kind === "register" && <RegisterDialog onClose={() => setDialog(null)} onSaved={saved} />}
      {dialog?.kind === "edit" && <EditDialog target={dialog.target} onClose={() => setDialog(null)} onSaved={saved} />}
      {dialog?.kind === "sell" && <SellDialog target={dialog.target} onClose={() => setDialog(null)} onSaved={saved} />}
    </>
  );
}
