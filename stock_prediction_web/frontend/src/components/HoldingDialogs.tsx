// 保有銘柄の登録・編集・売却のダイアログ。入力のチェックは画面と API の両方で行う
import { useState } from "react";

import { apiGet, apiSend, toMessage } from "../api/client";
import type { HoldingSaved, Lot, StockItem } from "../api/types";
import { displayCode, formatSignedNumber, formatSignedPercent, signClass } from "../lib/format";
import { checkLot, parseNumber, todayIso } from "../lib/holdingInput";
import DateInput from "./DateInput";
import Modal from "./Modal";

interface Common {
  onClose: () => void;
  /** 保存できたら呼ぶ(メッセージを渡す) */
  onSaved: (message: string) => void;
}

/** 登録(買い増しも、購入ごとに別の行として登録する) */
export function RegisterDialog({ onClose, onSaved }: Common) {
  const [code, setCode] = useState("");
  const [stock, setStock] = useState<StockItem | null>(null);
  const [choices, setChoices] = useState<StockItem[]>([]);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [buyDate, setBuyDate] = useState(todayIso());
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  /** 入力欄からフォーカスが外れたら、入れた文字で始まる銘柄を前方一致で探す */
  async function lookup() {
    const text = code.trim();
    setStock(null);
    setChoices([]);
    setCodeError(null);
    if (!text) return;
    try {
      const res = await apiGet<{ items: StockItem[] }>("/api/stocks/search", { q: text, scope: "all" });
      const hits = res.items.filter((i) => i.code.startsWith(text.toUpperCase()));
      if (hits.length === 1 && hits[0]) choose(hits[0]);
      else if (hits.length > 1) setChoices(hits);
      else setCodeError("銘柄が見つかりません");
    } catch (e) {
      setCodeError(toMessage(e));
    }
  }

  function choose(item: StockItem) {
    setStock(item);
    setChoices([]);
    setCode(displayCode(item.code));
    setCodeError(null);
  }

  async function save() {
    if (!stock) {
      setError("銘柄コードを入れて、銘柄を選んでください");
      return;
    }
    const p = parseNumber(price);
    const q = parseNumber(quantity);
    const problem = checkLot(p, q, buyDate);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    try {
      const res = await apiSend<HoldingSaved>("POST", "/api/holdings", {
        code: stock.code,
        buy_price: p,
        quantity: q,
        buy_date: buyDate,
        note: note.trim() || null,
      });
      onSaved(res.message);
    } catch (e) {
      setError(toMessage(e));
      setSaving(false);
    }
  }

  return (
    <Modal
      title="銘柄を登録"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn sub" onClick={onClose}>
            やめる
          </button>
          <button type="button" className="btn" onClick={() => void save()} disabled={saving}>
            登録する
          </button>
        </>
      }
    >
      <div className="form">
        <label htmlFor="reg-code">銘柄コード</label>
        <span className="inline">
          <input
            id="reg-code"
            value={code}
            style={{ width: 90 }}
            onChange={(e) => {
              setCode(e.target.value);
              setStock(null);
            }}
            onBlur={() => void lookup()}
          />
          {stock && (
            <>
              <b>{stock.name}</b>
              <span className="sub">({stock.market})</span>
            </>
          )}
          {codeError && <span className="err">{codeError}</span>}
        </span>
        {choices.length > 0 && (
          <>
            <span />
            <div className="choices" role="listbox" aria-label="銘柄の候補">
              <div className="choices-head">「{code}」の候補({choices.length}件)。選んでください</div>
              {choices.map((c) => (
                <button type="button" role="option" aria-selected={false} key={c.code} onClick={() => choose(c)}>
                  {displayCode(c.code)} {c.name}({c.market})
                </button>
              ))}
            </div>
          </>
        )}
        <label htmlFor="reg-price">買値(1株)</label>
        <input id="reg-price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
        <label htmlFor="reg-qty">買い株数</label>
        <input id="reg-qty" inputMode="numeric" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        <span>購入日</span>
        <DateInput label="購入日" value={buyDate} onChange={setBuyDate} />
        <label htmlFor="reg-note">メモ</label>
        <textarea id="reg-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        <span />
        <span className="sub">
          銘柄コードは4桁でも5桁でもよい。入力欄から離れると、入れた文字で始まる銘柄を探す(同じ銘柄の買い増しも、ここで登録する)
        </span>
      </div>
      {error && <p className="err">{error}</p>}
    </Modal>
  );
}

interface LotTarget {
  code: string;
  name: string;
  lot: Lot;
  /** 何回目の購入か(購入が1回だけなら null) */
  index: number | null;
}

/** 編集(銘柄は変えられない。買値・買い株数・購入日・メモ) */
export function EditDialog({ target, onClose, onSaved }: Common & { target: LotTarget }) {
  const [price, setPrice] = useState(String(target.lot.buy_price));
  const [quantity, setQuantity] = useState(String(target.lot.quantity));
  const [buyDate, setBuyDate] = useState(target.lot.buy_date);
  const [note, setNote] = useState(target.lot.note ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    const p = parseNumber(price);
    const q = parseNumber(quantity);
    const problem = checkLot(p, q, buyDate);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    try {
      const res = await apiSend<HoldingSaved>("PUT", `/api/holdings/${target.lot.holding_id}`, {
        buy_price: p,
        quantity: q,
        buy_date: buyDate,
        note: note.trim() || null,
      });
      onSaved(res.message);
    } catch (e) {
      setError(toMessage(e));
      setSaving(false);
    }
  }

  return (
    <Modal
      title="保有銘柄を編集"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn sub" onClick={onClose}>
            やめる
          </button>
          <button type="button" className="btn" onClick={() => void save()} disabled={saving}>
            保存する
          </button>
        </>
      }
    >
      <div className="form">
        <span>銘柄</span>
        <span className="fixed">
          {displayCode(target.code)} {target.name}(変更できません)
        </span>
        {target.index !== null && (
          <>
            <span>購入</span>
            <span>{target.index}回目の購入(購入ごとの行を直す)</span>
          </>
        )}
        <label htmlFor="edit-price">買値(1株)</label>
        <input id="edit-price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
        <label htmlFor="edit-qty">買い株数</label>
        <input id="edit-qty" inputMode="numeric" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        <span>購入日</span>
        <DateInput label="購入日" value={buyDate} onChange={setBuyDate} />
        <label htmlFor="edit-note">メモ</label>
        <textarea id="edit-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        <span />
        <span className="sub">銘柄を間違えた場合は、当面は SQL のひな形(etc\db\sql\holding_template.sql)で直す</span>
      </div>
      {error && <p className="err">{error}</p>}
    </Modal>
  );
}

/** 売却(売却株数を指定して、一部だけ売ることもできる) */
export function SellDialog({ target, onClose, onSaved }: Common & { target: LotTarget }) {
  const held = target.lot.quantity;
  const [sellDate, setSellDate] = useState(todayIso());
  const [quantity, setQuantity] = useState(String(held));
  const [price, setPrice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const q = parseNumber(quantity);
  const p = parseNumber(price);
  const valid = p > 0 && Number.isInteger(q) && q >= 1 && q <= held;
  const pnl = valid ? (p - target.lot.buy_price) * q : null;
  const pnlRate = valid ? (p - target.lot.buy_price) / target.lot.buy_price : null;

  async function save() {
    if (!sellDate || sellDate < target.lot.buy_date || sellDate > todayIso()) {
      setError("売却日は購入日以降で、今日以前の日付を入れてください");
      return;
    }
    if (!Number.isInteger(q) || q < 1 || q > held) {
      setError(`売却株数は1〜${held}株にしてください`);
      return;
    }
    if (!(p > 0)) {
      setError("売値は0より大きい値を入れてください");
      return;
    }
    setSaving(true);
    try {
      const res = await apiSend<HoldingSaved>("POST", `/api/holdings/${target.lot.holding_id}/sell`, {
        sell_date: sellDate,
        quantity: q,
        sell_price: p,
      });
      onSaved(res.message);
    } catch (e) {
      setError(toMessage(e));
      setSaving(false);
    }
  }

  return (
    <Modal
      title={`売却を登録: ${displayCode(target.code)} ${target.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn sub" onClick={onClose}>
            やめる
          </button>
          <button type="button" className="btn" onClick={() => void save()} disabled={saving}>
            売却を登録する
          </button>
        </>
      }
    >
      <div className="form">
        <span>保有</span>
        <span>
          {held}株 ／ 買値 {target.lot.buy_price.toLocaleString("ja-JP")}(購入日 {target.lot.buy_date})
        </span>
        <span>売却日</span>
        <DateInput label="売却日" value={sellDate} onChange={setSellDate} />
        <label htmlFor="sell-qty">売却株数</label>
        <span className="inline">
          <input
            id="sell-qty"
            inputMode="numeric"
            style={{ width: 90 }}
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
          株
          <button type="button" className="btn sm sub" onClick={() => setQuantity(String(held))}>
            全部
          </button>
        </span>
        <label htmlFor="sell-price">売値(1株)</label>
        <input id="sell-price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
        <span>損益(見込み)</span>
        <span className={signClass(pnl)} data-testid="sell-pnl">
          {pnl === null ? "-" : `${formatSignedNumber(pnl)}(${formatSignedPercent(pnlRate)})`}
        </span>
        {valid && q < held && (
          <>
            <span />
            <span className="partial">
              一部の売却です。売った{q}株は「売却済み」に移り、残りの{held - q}株は保有中のまま残ります
            </span>
          </>
        )}
      </div>
      {error && <p className="err">{error}</p>}
    </Modal>
  );
}

export type { LotTarget };
