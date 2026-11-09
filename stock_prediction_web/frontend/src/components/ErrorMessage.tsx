// エラーのメッセージ(赤い枠)

export default function ErrorMessage({ message }: { message: string }) {
  return (
    <div className="errbox" role="alert">
      {message}
    </div>
  );
}
