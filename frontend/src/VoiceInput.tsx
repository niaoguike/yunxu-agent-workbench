import React, { useRef, useState } from "react";
export function VoiceInput({
  onTranscript,
}: {
  onTranscript: (text: string) => void;
}) {
  const [listening, L] = useState(false),
    [error, E] = useState("");
  const current = useRef<any>(null);
  const start = () => {
    const Recognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    if (!Recognition) {
      E("此浏览器不支持语音输入，请使用文字");
      return;
    }
    const r = new Recognition();
    current.current = r;
    r.lang = "zh-CN";
    r.interimResults = false;
    r.onresult = (e: any) => onTranscript(e.results[0][0].transcript);
    r.onerror = () => {
      E("语音识别未完成，请检查麦克风权限");
      L(false);
    };
    r.onend = () => L(false);
    r.start();
    L(true);
    E("");
  };
  return (
    <div>
      <button
        type="button"
        onClick={() => (listening ? current.current?.stop() : start())}
      >
        {listening ? "停止录音" : "语音输入"}
      </button>
      <small>使用浏览器语音服务，识别后请核对再发送。</small>
      {error && <p role="status">{error}</p>}
    </div>
  );
}
