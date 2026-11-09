// ダイアログ(画面見本の .dialog)。画面の上に重ねて出し、Escape か「やめる」で閉じる
import { type ReactNode, useEffect } from "react";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** 下のボタン */
  footer: ReactNode;
}

export default function Modal({ title, onClose, children, footer }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay">
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
        <h3>{title}</h3>
        {children}
        <div className="foot">{footer}</div>
      </div>
    </div>
  );
}
