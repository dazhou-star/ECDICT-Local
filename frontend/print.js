(() => {
  const root=document.getElementById("paper");
  const mode=new URLSearchParams(location.search).get("mode")==="write"?"write":"read";
  const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const compactText=v=>String(v??"").replace(/\\r\\n|\\r|\\n|\r\n|\r|\n/g," | ");
  const dateLabel=v=>{const d=new Date(v+"T00:00:00");return Number.isNaN(d.getTime())?v:d.toLocaleDateString("zh-CN",{month:"2-digit",day:"2-digit"});};
  fetch("/api/print/"+mode+"/data").then(r=>{if(!r.ok)throw new Error("HTTP "+r.status);return r.json();}).then(data=>{
    document.title=(mode==="read"?"读单词本":"写单词本")+" · 复习纸条";
    const days=data.days||[];
    let body="";
    if(mode==="read"){
      body=days.map(day=>`<section class="day"><h2>${esc(day.label)} · ${dateLabel(day.date)}</h2>${day.words.length?day.words.map((w,i)=>`<article class="word"><b>${esc(w.word)}</b><span class="phonetic">${esc(w.phonetic||w.phonetic_us||"")}</span><small>${esc(w.pos||"")}</small><p>${esc(compactText(w.translation||w.definition||""))}</p></article>`).join(""):'<p class="muted">暂无到期单词</p>'}</section>`).join("");
    }else{
      let index=0;
      body=days.map(day=>`<section class="day"><h2>${esc(day.label)} · ${dateLabel(day.date)}</h2>${day.exercises.length?day.exercises.map(item=>`<article class="exercise"><div class="prompt">${esc(compactText(item.prompt))}</div><small>${esc(item.pos||"")}</small><div class="blank">${++index}. __________________________</div></article>`).join(""):'<p class="muted">暂无到期单词</p>'}</section>`).join("");
      const answers=days.flatMap(d=>d.answers||[]);
      body+=`<section class="answer-key"><h2>答案 · ${answers.length} 题</h2>${answers.map((a,i)=>`<p>${i+1}. <b>${esc(a.word)}</b> <small>${esc(compactText(a.prompt))}</small></p>`).join("")}</section>`;
    }
    root.innerHTML=`<header class="paper-head"><h1>${mode==="read"?"读单词本复习":"写单词本拼写练习"}</h1><p>${esc(data.week_start)} — ${esc(data.week_end)}</p><p>共 ${data.counts.words} 个单词 · ${mode==="write"?data.counts.exercises+" 道拼写题":"阅读复习清单"}</p></header>${body||'<p>本周没有安排复习内容。</p>'}<footer>LEXICON · LOCAL VOCABULARY REVIEW</footer>`;
  }).catch(e=>{root.innerHTML='<p class="error">纸条生成失败：'+esc(e.message)+'</p>';});
})();