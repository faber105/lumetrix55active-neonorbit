import { useCallback, useEffect, useState } from "react";
import { apiFetch, postJson } from "./api";

const cardStyle={padding:"18px",borderRadius:"18px",background:"rgba(13,16,36,.72)",border:"1px solid rgba(151,92,255,.26)",display:"grid",gap:"14px"};
const rowStyle={display:"grid",gap:"8px"};
const inputStyle={width:"100%",boxSizing:"border-box",padding:"13px 14px",borderRadius:"12px",border:"1px solid rgba(255,255,255,.13)",background:"rgba(0,0,0,.24)",color:"#fff",fontSize:"12px",outline:"none"};
const buttonStyle={padding:"12px 14px",border:0,borderRadius:"12px",fontWeight:800,cursor:"pointer",background:"linear-gradient(135deg,#8e45ff,#35d6dc)",color:"#fff"};

export default function PocketCredentialSettings(){
  const[state,setState]=useState(null),[demo,setDemo]=useState(""),[real,setReal]=useState(""),[busy,setBusy]=useState(""),[message,setMessage]=useState("");
  const load=useCallback(async()=>{try{setState(await apiFetch("/api/settings/pocket-credentials",{timeoutMs:8000}));}catch(e){setMessage(e?.message||"Не удалось загрузить статус Pocket")}},[]);
  useEffect(()=>{load()},[load]);
  const save=async(mode,value,setValue)=>{if(!value.trim())return setMessage(`Вставь ${mode.toUpperCase()} SSID полностью`);setBusy(mode);setMessage("");try{const next=await postJson("/api/settings/pocket-credentials",{mode,ssid:value.trim()});setState({selected_mode:next.selected_mode,credentials:next.credentials});setValue("");setMessage(`${mode.toUpperCase()} сессия сохранена${next.reconnect?" · worker переподключается":""}`)}catch(e){setMessage(e?.body?.detail||e?.message||"Ошибка сохранения")}finally{setBusy("")}};
  const status=(mode)=>state?.credentials?.[mode]?.configured?"ПОДКЛЮЧЕНА":"НЕ ЗАДАНА";
  return <section className="glass" style={cardStyle}>
    <div><small style={{opacity:.65}}>POCKET OPTION</small><h2 style={{margin:"4px 0 2px"}}>Сессии DEMO / REAL</h2><p style={{margin:0,opacity:.68,fontSize:"13px",lineHeight:1.45}}>Вставляй полный SSID/seed из Pocket. Значение хранится зашифрованно и в интерфейс обратно не выводится.</p></div>
    <div style={rowStyle}><label><b>DEMO · {status("demo")}</b></label><textarea rows="3" spellCheck="false" autoComplete="off" value={demo} onChange={e=>setDemo(e.target.value)} placeholder={'42["auth",{"session":"...","isDemo":1}]'} style={inputStyle}/><button type="button" disabled={busy!==""} onClick={()=>save("demo",demo,setDemo)} style={buttonStyle}>{busy==="demo"?"Проверяю и подключаю…":"Сохранить DEMO"}</button></div>
    <div style={rowStyle}><label><b>REAL · {status("real")}</b></label><textarea rows="3" spellCheck="false" autoComplete="off" value={real} onChange={e=>setReal(e.target.value)} placeholder={'42["auth",{"sessionToken":"..."}]'} style={inputStyle}/><button type="button" disabled={busy!==""} onClick={()=>save("real",real,setReal)} style={buttonStyle}>{busy==="real"?"Проверяю и подключаю…":"Сохранить REAL"}</button></div>
    <div style={{fontSize:"12px",opacity:.72}}>Активный режим: <b>{String(state?.selected_mode||"demo").toUpperCase()}</b>. При сохранении активной сессии worker закрывает старое соединение и использует новое автоматически.</div>
    {message&&<div style={{padding:"10px 12px",borderRadius:"10px",background:"rgba(255,255,255,.06)",fontSize:"12px"}}>{message}</div>}
  </section>
}
