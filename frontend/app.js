

(() => {

  const $ = s => document.querySelector(s);



  const state = {

    mode: "prefix",

    page: "lookup",

    timer: null,

    selected: -1,

    favorites: [],
    favoriteDates: {},
    bookWords: { read: [], write: [] },
    bookDates: { read: {}, write: {} },
    bookUnknown: { read: {}, write: {} },

    history: read("lexicon-history", []),

    audio: null,


  };



  const q = $("#query");

  const suggestions = $("#suggestions");



  function read(k, fallback) {

    try {

      return JSON.parse(localStorage.getItem(k)) ?? fallback;

    } catch {

      return fallback;

    }

  }



  function save(k, v) {

    localStorage.setItem(k, JSON.stringify(v));

  }



  function esc(v) {

    return String(v ?? "").replace(/[&<>"']/g, c => ({

      "&": "&amp;",

      "<": "&lt;",

      ">": "&gt;",

      '"': "&quot;",

      "'": "&#39;"

    }[c]));

  }



  async function api(path, options = {}) {
    const r = await fetch(path, options);
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        detail = (await r.json()).detail || detail;
      } catch {}
      throw new Error(detail);
    }
    return r.json();
  }


  function status(s) {

    $("#status").textContent = s;

  }



  function addHistory(word) {

    state.history = [

      word,

      ...state.history.filter(x => x !== word)

    ].slice(0, 50);

    save("lexicon-history", state.history);

  }



  function applyFavorites(items) {
    state.favorites = items.map(item => item.word);
    state.favoriteDates = Object.fromEntries(
      items.map(item => [item.word.toLowerCase(), item.study_date || item.added_at])
    );
    $("#favCount").textContent = state.favorites.length;
  }

  async function loadFavorites() {
    const data = await api("/api/favorites");
    applyFavorites(Array.isArray(data.items) ? data.items : []);
    return data.items || [];
  }

  async function initializeFavorites() {
    // Migrate the previous browser-only collection. Do not remove its local
    // copy until the server import and a subsequent read both succeed.
    const legacy = read("lexicon-favorites", []);
    if (!Array.isArray(legacy)) {
      throw new Error("旧生词本数据格式异常，未清理本地数据");
    }

    if (legacy.length) {
      await api("/api/favorites/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ words: legacy })
      });
    }

    await loadFavorites();
    localStorage.removeItem("lexicon-favorites");
  }

  async function loadBook(mode) {
    const data = await api(`/api/books/${mode}`);
    const items = Array.isArray(data.items) ? data.items : [];
    state.bookWords[mode] = items.map(item => item.word);
    state.bookDates[mode] = Object.fromEntries(items.map(item => [
      item.word.toLowerCase(), item.study_date || item.added_at
    ]));
    state.bookUnknown[mode] = Object.fromEntries(items.map(item => [
      item.word.toLowerCase(), Boolean(item.is_unknown)
    ]));
    const count = document.getElementById(mode === "read" ? "readBookCount" : "writeBookCount");
    if (count) count.textContent = items.length;
    return items;
  }

  async function initializeVocabularyBooks() {
    const legacy = read("lexicon-favorites", []);
    if (!Array.isArray(legacy)) throw new Error("旧生词本数据格式异常，未清理本地数据");
    if (legacy.length) {
      await api("/api/books/read/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ words: legacy })
      });
    }
    await Promise.all([loadBook("read"), loadBook("write")]);
    localStorage.removeItem("lexicon-favorites");
  }

  function isInBook(mode, word) {
    return (state.bookWords[mode] || []).some(
      saved => saved.toLowerCase() === String(word).toLowerCase()
    );
  }

  async function toggleBookWord(mode, word) {
    const saved = isInBook(mode, word);
    try {
      if (saved) {
        await api(`/api/books/${mode}/${encodeURIComponent(word)}`, { method: "DELETE" });
      } else {
        await api(`/api/books/${mode}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ word })
        });
      }
      await loadBook(mode);
      await showWord(word);
      status(saved
        ? `已从${mode === "read" ? "读" : "写"}单词本移除 ${word}`
        : `已加入${mode === "read" ? "读" : "写"}单词本：${word}`);
    } catch (error) {
      status("单词本操作失败：" + error.message);
    }
  }

  async function setWordUnknown(word, unknown, mode=null, questionId=null) {
    return api(`/api/words/${encodeURIComponent(word)}/unknown`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unknown, mode, question_id: questionId })
    });
  }


  async function showBook(mode) {
    await loadBook(mode);
    const page = mode === "read" ? "read-book" : "write-book";
    state.page = page;
    setSide(page);
    hideSuggestions();
    $("#welcome").hidden = true;
    $("#empty").hidden = true;
    const title = mode === "read" ? "读单词本" : "写单词本";
    const subtitle = mode === "read"
      ? "用于阅读理解：看到英文时能够理解中文含义。此单词本不安排读方向测验。"
      : "用于主动输出：看到中文释义时能够写出对应英文。后续写词测验与复习将在此单词本中进行。";
    $("#results").innerHTML = `
      <section class="collection-panel">
        <header class="collection-header">
          <div class="collection-heading"><div class="collection-kicker">${mode.toUpperCase()} VOCABULARY</div><h2>${title}</h2><p>${subtitle}</p></div>
          <div class="collection-total"><strong>${state.bookWords[mode].length}</strong><span>个单词</span></div>
        </header>
        <div class="collection-toolbar">
          <label class="collection-search"><span>⌕</span><input id="bookSearch" type="search" placeholder="筛选单词…" autocomplete="off"></label>
          <label class="collection-sort"><span>排序</span><select id="bookSort"><option value="recent">最近添加</option><option value="az">A → Z</option><option value="za">Z → A</option></select></label>
        </div>
        <div class="collection-summary"><span id="bookSummary"></span><span>点击单词查看释义 · 可单独从本单词本移除</span></div>
        <div class="collection-grid" id="bookGrid"></div>
        <div class="collection-empty collection-no-results" id="bookEmpty" hidden><h3>单词本还是空的</h3><p>查词后，可以分别将单词加入读单词本或写单词本。</p></div>
      </section>`;
    const search = $("#bookSearch"), sort = $("#bookSort"), grid = $("#bookGrid"), empty = $("#bookEmpty");
    const render = () => {
      let items = state.bookWords[mode].map(word => ({
        word, study_date: state.bookDates[mode][word.toLowerCase()] || ""
      })).filter(item => item.word.toLowerCase().includes(search.value.trim().toLowerCase()));
      if (sort.value === "az") items.sort((a,b)=>a.word.localeCompare(b.word));
      if (sort.value === "za") items.sort((a,b)=>b.word.localeCompare(a.word));
      $("#bookSummary").textContent = `显示 ${items.length} / ${state.bookWords[mode].length} 个单词`;
      grid.innerHTML = items.map((item,i)=>`
        <article class="collection-word-card">
          <button class="collection-word-open" data-open-word="${esc(item.word)}"><span class="collection-word-main"><span class="collection-word">${esc(item.word)}</span><span class="collection-word-index">${String(i+1).padStart(2,"0")}</span></span><span class="collection-word-meta">添加日期 ${esc(formatFavoriteDate(item.study_date))}</span></button>
          <button class="collection-action ${state.bookUnknown[mode][item.word.toLowerCase()] ? "unknown-active" : "unknown"}" data-unknown-word="${esc(item.word)}">${state.bookUnknown[mode][item.word.toLowerCase()] ? "不认识 · 取消" : "不认识"}</button>
          <button class="collection-action remove" data-remove-word="${esc(item.word)}">移除</button>
        </article>`).join("");
      empty.hidden = items.length > 0;
      grid.hidden = items.length === 0;
      grid.querySelectorAll("[data-open-word]").forEach(b=>b.addEventListener("click",()=>showWord(b.dataset.openWord)));
      grid.querySelectorAll("[data-unknown-word]").forEach(b=>b.addEventListener("click",async()=>{
        const word=b.dataset.unknownWord, unknown=!state.bookUnknown[mode][word.toLowerCase()];
        b.disabled=true;
        try { await setWordUnknown(word,unknown); await loadBook(mode); render(); }
        catch(e){b.disabled=false;status("标记失败："+e.message);}
      }));
      grid.querySelectorAll("[data-remove-word]").forEach(b=>b.addEventListener("click",async()=>{
        b.disabled=true;
        try { await api(`/api/books/${mode}/${encodeURIComponent(b.dataset.removeWord)}`,{method:"DELETE"}); await loadBook(mode); render(); }
        catch(e){b.disabled=false;status("移除失败："+e.message);}
      }));
    };
    search.addEventListener("input",render); sort.addEventListener("change",render); render();
  }


  async function showMastery(mode="read") {
    mode = mode === "write" ? "write" : "read";
    state.page="mastery"; setSide("mastery"); hideSuggestions();
    $("#welcome").hidden=true; $("#empty").hidden=true;
    $("#results").innerHTML='<section class="mastery-panel">正在加载…</section>';
    try {
      const pair=await Promise.all([api("/api/mastery/"+mode+"/overview"),api("/api/mastery/"+mode+"/senses")]);
      const o=pair[0],d=pair[1];
      $("#results").innerHTML='<section class="mastery-panel">'+
        '<header class="mastery-heading"><div><div class="collection-kicker">MASTERY & REVIEW</div><h2>掌握度与复习计划</h2><p>这里只管理义项状态和独立复习计划；检测已移至独立页面。</p></div><a href="/print.html?mode='+mode+'" target="_blank">纸条打印 ↗</a></header>'+
        '<div class="mastery-switch"><button data-mm="read" class="'+(mode==="read"?"active":"")+'">📖 读单词本</button><button data-mm="write" class="'+(mode==="write"?"active":"")+'">✎ 写单词本</button></div>'+
        '<div class="mastery-stats"><div><b>'+o.words+'</b><span>单词</span></div><div><b>'+o.active+'</b><span>有效义项</span></div><div><b>'+o.due+'</b><span>待复习义项</span></div><div><b>'+o.tests+'</b><span>检测记录</span></div></div>'+
        '<h3>义项复习状态</h3><div class="mastery-sense-list">'+(d.items.map(x=>'<article class="mastery-sense"><div><b>'+esc(x.word)+'</b> <small>'+esc(x.pos||"")+'</small><p>'+esc(x.fragments||x.sense_text||"")+'</p></div><div class="mastery-sense-meta"><span>下次复习：'+esc(x.next_review_date||"未安排")+'</span><span>连续正确 '+(x.streak||0)+' 次 · 正确 '+(x.total_correct||0)+' / 错误 '+(x.total_wrong||0)+'</span><select data-sid="'+x.id+'"><option value="active" '+(x.review_state==="active"?"selected":"")+'>纳入复习</option><option value="draft" '+(x.review_state==="draft"?"selected":"")+'>暂存</option><option value="ignored" '+(x.review_state==="ignored"?"selected":"")+'>忽略</option></select></div></article>').join("")||"<p>暂无义项</p>")+'</div></section>';
      document.querySelectorAll("[data-mm]").forEach(b=>b.onclick=()=>showMastery(b.dataset.mm));
      document.querySelectorAll("[data-sid]").forEach(el=>el.onchange=async()=>{try{await api("/api/mastery/"+mode+"/senses/"+el.dataset.sid+"/state",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({state:el.value})});showMastery(mode);}catch(e){status(e.message);}});
    } catch(e) {$("#results").innerHTML='<section class="mastery-panel">加载失败：'+esc(e.message)+'</section>';}
  }

  async function showReadTest() {
    state.page="read-test"; setSide("read-test"); hideSuggestions();
    $("#welcome").hidden=true; $("#empty").hidden=true;
    $("#results").innerHTML='<section class="test-workspace"><header class="test-heading"><div class="collection-kicker">READING · MULTIPLE CHOICE</div><h2>读词检测</h2><p>看到英文单词，选择最符合当前义项的中文解释。每个义项独立记录掌握度。</p></header><div id="readTestStats" class="test-stats">正在读取复习状态…</div><div class="test-actions"><label class="test-include-future"><input id="readIncludeFuture" type="checkbox"> 包含未到期单词</label><button id="readTestStart" class="primary-action">开始检测</button></div><div id="readTestHost"></div></section>';
    try {const o=await api("/api/mastery/read/overview");$("#readTestStats").innerHTML='<span>到期义项 <b>'+o.due+'</b></span><span>累计检测 <b>'+o.tests+'</b></span>';} catch(e) {$("#readTestStats").textContent="状态读取失败："+e.message;}
    $("#readTestStart").onclick=nextReadQuestion;
  }

  async function nextReadQuestion() {
    const host=$("#readTestHost"); if(!host)return;
    host.innerHTML='<p class="test-wait">正在抽取检测义项…</p>';
    try {
      const includeFuture=$("#readIncludeFuture")?.checked;
      const q=await api("/api/mastery/read/question"+(includeFuture?"?include_future=true":""));
      if(q.done){host.innerHTML='<div class="test-complete"><h3>'+esc(q.message||"当前没有待检测义项")+'</h3><p>'+(q.next_review_date?"下次复习："+esc(q.next_review_date):"请先在掌握度页面启用义项。")+'</p></div>';return;}
      host.innerHTML='<article class="quiz-card"><div class="quiz-meta">读单词本 · 选择最符合的中文含义 · '+esc(q.pos||"")+'</div><h3>'+esc(q.word)+'</h3><p>这个单词在当前义项中表示：</p><div class="quiz-options">'+q.options.map((option,i)=>'<button data-read-answer="'+i+'"><span>'+String.fromCharCode(65+i)+'</span><div class="quiz-option-text">'+esc(option)+'</div></button>').join("")+'</div><div id="readQuizFeedback"></div><div class="quiz-unknown-row"><button type="button" class="mark-unknown" id="readMarkUnknown">'+(q.is_unknown?"已标记不认识":"标记为不认识")+'</button></div><button class="quiz-next" id="readQuizNext" hidden>下一题</button></article>';
      $("#readMarkUnknown").onclick=async()=>{
        const b=$("#readMarkUnknown");b.disabled=true;
        try{
          await setWordUnknown(q.word,true,"read",q.question_id);
          $("#readQuizFeedback").innerHTML='<div class="quiz-result wrong"><b>已标记为不认识</b><p>已加入优先复习，并记录为一次未掌握。</p></div>';
          host.querySelectorAll("[data-read-answer]").forEach(x=>x.disabled=true);
          b.textContent="已标记不认识";
          $("#readQuizNext").hidden=false;$("#readQuizNext").onclick=nextReadQuestion;
          const o=await api("/api/mastery/read/overview");$("#readTestStats").innerHTML='<span>到期义项 <b>'+o.due+'</b></span><span>累计检测 <b>'+o.tests+'</b></span>';
        }catch(e){b.disabled=false;status("标记失败："+e.message);}
      };
      host.querySelectorAll("[data-read-answer]").forEach(button=>button.onclick=async()=>{
        if(button.disabled)return;
        host.querySelectorAll("[data-read-answer]").forEach(x=>x.disabled=true);
        const answer=q.options[Number(button.dataset.readAnswer)];
        try{
          const r=await api("/api/mastery/read/answer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question_id:q.question_id,answer:answer})});
          $("#readQuizFeedback").innerHTML='<div class="quiz-result '+(r.correct?"correct":"wrong")+'"><b>'+(r.correct?"回答正确":"回答错误")+'</b><p>正确释义：'+esc(r.correct_answer)+'</p><small>连续答对：'+r.streak+' 次 · 下次复习：'+esc(r.next_review_date)+'（'+r.interval_days+' 天后）</small></div>';
          $("#readQuizNext").hidden=false;$("#readQuizNext").onclick=nextReadQuestion;
          const o=await api("/api/mastery/read/overview");$("#readTestStats").innerHTML='<span>到期义项 <b>'+o.due+'</b></span><span>累计检测 <b>'+o.tests+'</b></span>';
        }catch(e){$("#readQuizFeedback").textContent="提交失败："+e.message;}
      });
    }catch(e){host.innerHTML='<p class="test-wait">检测暂不可用：'+esc(e.message)+'</p>';}
  }

  async function showWriteTest() {
    state.page="write-test"; setSide("write-test"); hideSuggestions();
    $("#welcome").hidden=true; $("#empty").hidden=true;
    $("#results").innerHTML='<section class="test-workspace write-test-workspace"><header class="test-heading"><div class="collection-kicker">WRITING · ACTIVE RECALL</div><h2>写词检测</h2><p>根据明确的中文释义写出目标英文单词。同义词不会直接判错，系统会逐步给出线索。</p></header><div id="writeTestStats" class="test-stats">正在读取复习状态…</div><div class="test-actions"><label class="test-include-future"><input id="writeIncludeFuture" type="checkbox"> 包含未到期单词</label><button id="writeTestStart" class="primary-action">开始拼写检测</button></div><div id="writeTestHost"></div></section>';
    try {const o=await api("/api/mastery/write/overview");$("#writeTestStats").innerHTML='<span>到期义项 <b>'+o.due+'</b></span><span>累计检测 <b>'+o.tests+'</b></span>';} catch(e) {$("#writeTestStats").textContent="状态读取失败："+e.message;}
    $("#writeTestStart").onclick=loadWriteQuestion;
  }

  async function loadWriteQuestion(){
    const host=$("#writeTestHost");if(!host)return;
    host.innerHTML='<p class="test-wait">正在准备拼写题…</p>';
    try{
      const includeFuture=$("#writeIncludeFuture")?.checked;
      const q=await api("/api/mastery/write/question"+(includeFuture?"?include_future=true":""),{method:"POST"});
      if(q.done){host.innerHTML='<div class="test-complete"><h3>'+esc(q.message||"当前没有待复习义项")+'</h3><p>'+(q.next_review_date?"下次复习："+esc(q.next_review_date):"请先在掌握度页面启用写单词本义项。")+'</p></div>';return;}
      host.innerHTML='<article class="write-test-card"><div class="write-test-meta"><span>写单词本 · 目标词拼写</span><span>'+esc(q.due_date||"")+'</span></div><div class="write-test-prompt-label">请根据下面的释义写出对应英文单词</div><div class="write-test-prompt">'+esc(q.prompt)+'</div><div class="write-test-pos">'+esc(q.pos||"词性未标注")+'</div><form id="writeTestForm" class="write-test-form"><input id="writeTestAnswer" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="输入英文单词…" required><button class="primary-action">提交</button></form><div id="writeTestFeedback" aria-live="polite"></div><div class="quiz-unknown-row"><button type="button" class="mark-unknown" id="writeMarkUnknown">'+(q.is_unknown?"已标记不认识":"标记为不认识")+'</button></div><button id="writeTestNext" class="quiz-next" hidden>下一题</button></article>';
      const input=$("#writeTestAnswer");
      input.focus();
      $("#writeMarkUnknown").onclick=async()=>{
        const b=$("#writeMarkUnknown");b.disabled=true;
        try{
          await setWordUnknown(q.word,true,"write",q.question_id);
          input.disabled=true;$("#writeTestForm").querySelector("button").disabled=true;
          $("#writeTestFeedback").innerHTML='<div class="quiz-result wrong"><b>已标记为不认识</b><p>已加入优先复习，并记录为一次未掌握。</p></div>';
          b.textContent="已标记不认识";
          $("#writeTestNext").hidden=false;$("#writeTestNext").onclick=loadWriteQuestion;
          const o=await api("/api/mastery/write/overview");$("#writeTestStats").innerHTML='<span>到期义项 <b>'+o.due+'</b></span><span>累计检测 <b>'+o.tests+'</b></span>';
        }catch(e){b.disabled=false;status("标记失败："+e.message);}
      };
      $("#writeTestForm").onsubmit=async e=>{
        e.preventDefault();const form=e.currentTarget,button=form.querySelector("button");button.disabled=true;
        try{
          const r=await api("/api/mastery/write/guess",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question_id:q.question_id,answer:input.value})});
          const feedback=$("#writeTestFeedback");
          if(r.accepted_synonym&&!r.finished){
            feedback.innerHTML='<div class="write-feedback synonym"><b>意思符合，不算错</b><p>'+esc(r.feedback)+'</p><p class="write-hint">'+esc(r.hint)+'</p></div>';
            input.value="";button.disabled=false;input.focus();
          }else{
            input.disabled=true;button.disabled=true;
            feedback.innerHTML='<div class="quiz-result '+(r.correct?"correct":"wrong")+'"><b>'+(r.correct?"拼写正确":"本题未答对")+'</b><p>'+(r.correct?"很好！":"正确答案："+esc(r.correct_answer))+'</p><small>下次复习：'+esc(r.next_review_date||"")+' · 连续正确 '+(r.streak||0)+' 次</small></div>';
            $("#writeTestNext").hidden=false;$("#writeTestNext").onclick=loadWriteQuestion;
            const o=await api("/api/mastery/write/overview");$("#writeTestStats").innerHTML='<span>到期义项 <b>'+o.due+'</b></span><span>累计检测 <b>'+o.tests+'</b></span>';
          }
        }catch(err){button.disabled=false;$("#writeTestFeedback").textContent="提交失败："+err.message;}
      };
    }catch(e){host.innerHTML='<p class="test-wait">无法生成题目：'+esc(e.message)+'</p>';}
  }

  function showPrintChooser(){
    state.page="print";setSide("print");hideSuggestions();$("#welcome").hidden=true;$("#empty").hidden=true;
    $("#results").innerHTML='<section class="mastery-panel"><h2>纸条打印</h2><p>独立的 80mm 纯纸张页面，无需应用内打印按钮。</p><div class="print-mode-options"><a href="/print.html?mode=read" target="_blank">📖 读单词本纸条 <small>复习清单，不含检测题</small></a><a href="/print.html?mode=write" target="_blank">✎ 写单词本练习纸 <small>中文提示、英文填空、答案置于末尾</small></a></div></section>';
  }

  function isSaved(word) {
    return state.favorites.some(
      savedWord => savedWord.toLowerCase() === String(word).toLowerCase()
    );
  }

  async function toggleSave(word) {
    const button = $("#save");
    if (button) button.disabled = true;

    try {
      if (isSaved(word)) {
        await api(`/api/favorites/${encodeURIComponent(word)}`, {
          method: "DELETE"
        });
      } else {
        await api("/api/favorites", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ word })
        });
      }

      await loadFavorites();
      await showWord(word);
    } catch (e) {
      status(`生词本操作失败：${e.message}`);
      console.error(e);
      if (button && button.isConnected) button.disabled = false;
    }
  }

  function formatFavoriteDate(value) {
    if (!value) return "日期未知";
    const raw = String(value).slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return raw;
    return date.toLocaleDateString(undefined, { year: "numeric", month: "2-digit", day: "2-digit" });
  }

  function localDateInputValue() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  }


  function hideSuggestions() {

    suggestions.hidden = true;

    suggestions.innerHTML = "";

    state.selected = -1;

  }



  async function doSuggest() {

    const term = q.value.trim();



    $("#clear").style.display = term ? "block" : "none";



    if (!term || state.page !== "lookup") {

      hideSuggestions();

      return;

    }



    try {

      const data = await api(

        `/api/search?q=${encodeURIComponent(term)}&limit=12&mode=${state.mode}`

      );



      suggestions.innerHTML = "";



      data.items.forEach(item => {

        const b = document.createElement("button");

        b.className = "suggestion";

        b.innerHTML = `

          <b>${esc(item.word)}</b>

          <small>${item.match_type === "fuzzy" ? '<em class="suggestion-match">近似匹配</em> ' : ""}${esc(item.translation || item.definition || "查看词条")}</small>

        `;



        b.addEventListener("mousedown", e => e.preventDefault());

        b.addEventListener("click", () => showWord(item.word));

        suggestions.appendChild(b);

      });



      suggestions.hidden = !data.items.length;

      state.selected = -1;

    } catch (e) {

      status("搜索服务不可用");

      console.error(e);

    }

  }



  function section(title, value, cls = "translation") {

    if (value === null || value === undefined || value === "") return "";



    return `

      <section class="section">

        <div class="section-title">${esc(title)}</div>

        <div class="${cls}">${esc(value)}</div>

      </section>

    `;

  }



  function fieldLabel(k) {

    return ({

      word: "单词",

      phonetic: "音标",

      phonetic_us: "美式音标",

      phonetic_uk: "英式音标",

      definition: "英文释义",

      translation: "中文释义",

      pos: "词性",

      collins: "柯林斯星级",

      oxford: "牛津核心词汇",

      tag: "词汇标签",

      bnc: "BNC 词频",

      frq: "词频",

      exchange: "词形变化",

      audio: "发音音频"

    })[k] || k;

  }



  // 兼容数据库中的真实换行与字面量 \n
  function normalizeNewlines(value) {
    let text = String(value ?? "");
    // Handle CRLF/LF that were stored as literal backslash sequences.
    // Repeat twice to also cover values escaped once more during transport.
    for (let i = 0; i < 2; i++) {
      text = text
        .replace(/\\\\r\\\\n/g, "\n")
        .replace(/\\\\n/g, "\n")
        .replace(/\\r\\n/g, "\n")
        .replace(/\\n/g, "\n");
    }
    return text.replace(/\r\n?/g, "\n");
  }

  // 中文释义按词性分组
  function parseTranslation(value) {
    const lines = normalizeNewlines(value)
      .split("\n")
      .map(line => line.trim())
      .filter(Boolean);

    const groups = [];
    const pattern = /^((?:n|v|vt|vi|a|s|r|adj|adv|pron|prep|conj|int|art|aux|abbr|num|det)\.|\[[^\]]+\])\s*(.*)$/i;

    for (const line of lines) {
      const match = line.match(pattern);
      if (match) {
        groups.push({ label: match[1], text: match[2] });
      } else if (groups.length) {
        groups[groups.length - 1].text +=
          (groups[groups.length - 1].text ? " " : "") + line;
      } else {
        groups.push({ label: "释义", text: line });
      }
    }
    return groups;
  }

    // ECDICT exchange 字段解析
  function parseExchange(value) {
    if (!value) return [];
    return String(value).split("/").map(part => {
      const index = part.indexOf(":");
      if (index < 0) return null;
      const code = part.slice(0, index).trim();
      const word = part.slice(index + 1).trim();
      return word ? { code, word } : null;
    }).filter(Boolean);
  }

  function getRegularVerbForms(word) {
    const w = String(word ?? "").trim().toLowerCase();
    if (!w) return null;

    const vowelGroups = w.match(/[aeiou]+/g) || [];
    const cvcEnding = /[^aeiou][aeiou][^aeiouwxy]$/.test(w);
    const doublesFinalConsonant = cvcEnding && vowelGroups.length === 1;
    const doublesFinalZ = /[aeiou]z$/.test(w) && !w.endsWith("zz");
    const finalLVariant =
      !w.endsWith("ll") && /[^aeiou][aeiou]l$/.test(w);

    let past;
    if (w.endsWith("e")) {
      past = [w + "d"];
    } else if (/[^aeiou]y$/.test(w)) {
      past = [w.slice(0, -1) + "ied"];
    } else if (doublesFinalZ) {
      past = [w + "zed"];
    } else if (w.endsWith("c")) {
      past = [w + "ked"];
    } else {
      past = [w + "ed"];
      if (doublesFinalConsonant) {
        past = [w + w.slice(-1) + "ed"];
      } else if (cvcEnding) {
        past.push(w + w.slice(-1) + "ed");
      }
    }

    let ing;
    if (w === "be") {
      ing = ["being"];
    } else if (w.endsWith("ie")) {
      ing = [w.slice(0, -2) + "ying"];
    } else if (w.endsWith("e") && !w.endsWith("ee")) {
      ing = [w.slice(0, -1) + "ing"];
    } else if (doublesFinalZ) {
      ing = [w + "zing"];
    } else if (w.endsWith("c")) {
      ing = [w + "king"];
    } else {
      ing = [w + "ing"];
      if (doublesFinalConsonant) {
        ing = [w + w.slice(-1) + "ing"];
      } else if (cvcEnding) {
        ing.push(w + w.slice(-1) + "ing");
      }
    }

    if (finalLVariant) {
      past.push(w + "led");
      ing.push(w + "ling");
    }

    let third;
    if (doublesFinalZ) {
      third = w + "zes";
    } else if (/(s|x|z|ch|sh|o)$/.test(w)) {
      third = w + "es";
    } else if (/[^aeiou]y$/.test(w)) {
      third = w.slice(0, -1) + "ies";
    } else {
      third = w + "s";
    }

    return { p: past, d: past, i: ing, "3": [third] };
  }

  function getIrregularForms(baseWord, forms) {
    const expected = getRegularVerbForms(baseWord);
    if (!expected) return [];

    return ["p", "d", "i", "3"].filter(code => {
      const actual = forms[code];
      return actual &&
        !expected[code].includes(String(actual).trim().toLowerCase());
    });
  }

  function getDoubledVerbForms(baseWord, forms) {
    const base = String(baseWord ?? "").trim().toLowerCase();
    const expected = getRegularVerbForms(base);
    if (!expected) return [];

    const last = base.slice(-1);
    const doubleForms = {
      p: base + last + "ed",
      d: base + last + "ed",
      i: base + last + "ing"
    };
    if (/[aeiou]z$/.test(base) && !base.endsWith("zz")) {
      doubleForms["3"] = base + last + "es";
    }

    return Object.keys(doubleForms).filter(code => {
      const actual = String(forms[code] ?? "").trim().toLowerCase();
      return actual === doubleForms[code] &&
        expected[code].includes(actual);
    });
  }

  function isVerbEntry(pos) {
    return String(pos ?? "").split(/[;,/]+/).some(value =>
      ["v", "vt", "vi"].includes(
        value.trim().toLowerCase().replace(/\.$/, "")
      )
    );
  }

  const POS_NAMES = {
    n: "名词", v: "动词", vt: "及物动词", vi: "不及物动词",
    a: "形容词", s: "形容词", r: "副词",
    adj: "形容词", adv: "副词", pron: "代词", prep: "介词",
    conj: "连词", int: "感叹词", art: "冠词", aux: "助动词",
    abbr: "缩写", num: "数词", det: "限定词"
  };

  function getPosKey(value) {
    const key = String(value ?? "")
      .trim()
      .toLowerCase()
      .replace(/\.$/, "");

    if (["v", "vt", "vi"].includes(key)) return "verb";
    if (["a", "s", "adj"].includes(key)) return "adj";
    if (["r", "adv"].includes(key)) return "adv";

    const allowed = [
      "n", "pron", "prep", "conj", "int",
      "art", "aux", "abbr", "num", "det"
    ];

    return allowed.includes(key) ? key : "other";
  }

  function posClass(value) {
    return `pos-${getPosKey(value)}`;
  }

  const TAG_NAMES = {
    cet4: "大学英语四级", cet6: "大学英语六级",
    ky: "考研英语", toefl: "托福", ielts: "雅思", gre: "GRE",
    zk: "中考英语", gk: "高考英语"
  };

  function parseTags(value) {
    const raw = String(value ?? "").toLowerCase().trim();
    if (!raw) return [];

    // ECDICT may store tags as "zk gk" or as a compact concatenated string.
    const tokens = raw.split(/[\s,;|]+/).filter(Boolean);
    const known = ["cet4", "cet6", "toefl", "ielts", "gre", "ky", "zk", "gk"];
    const found = [];

    for (const token of tokens) {
      let rest = token;
      for (const tag of known) {
        if (rest.includes(tag)) {
          if (!found.includes(tag)) found.push(tag);
          rest = rest.split(tag).join("");
        }
      }
    }
    return found;
  }

  function renderTranslation(value) {
    const groups = parseTranslation(value);
    if (!groups.length) return "";
    return `
      <section class="section">
        <div class="section-title">中文释义</div>
        <div class="translation-groups">
          ${groups.map(group => `
            <div class="translation-group">
              <span class="pos-label ${posClass(group.label)}">${esc(group.label)}</span>
              <span class="meaning-text">${esc(group.text)}</span>
            </div>
          `).join("")}
        </div>
      </section>
    `;
  }

  function renderDefinitions(value) {
    const lines = normalizeNewlines(value)
      .split("\n").map(x => x.trim()).filter(Boolean);
    if (!lines.length) return "";

    const items = lines.map(line => {
      const match = line.match(/^((?:vt|vi|adj|adv|pron|prep|conj|int|art|aux|abbr|num|det|n|v|a|s|r)\.)\s*(.*)$/i);
      return match
        ? { pos: match[1], text: match[2] }
        : { pos: "", text: line };
    });

    return `
      <section class="section">
        <div class="section-title">英文释义</div>
        <ol class="definition-list">
          ${items.map(item => `
            <li class="definition-item">
              ${item.pos ? `<span class="definition-pos ${posClass(item.pos)}">${esc(item.pos)}</span>` : ""}
              <span class="definition-text">${esc(item.text)}</span>
            </li>
          `).join("")}
        </ol>
      </section>
    `;
  }

  function renderVocabulary(entry) {
    const pos = String(entry.pos ?? "").split(/[;,/]+/)
      .map(x => x.trim()).filter(Boolean);
    const tags = parseTags(entry.tag);
    if (!pos.length && !tags.length) return "";

    return `
      <section class="section">
        <div class="section-title">词性与词汇标签</div>
        ${pos.length ? `
          <div class="vocab-line">
            <span class="vocab-heading">词性</span>
            <span class="vocab-values">${pos.map(x => {
              const key = x.toLowerCase().replace(/\.$/, "");
              return `<span class="vocab-pos ${posClass(key)}">${esc(POS_NAMES[key] || x)} <small>${esc(x)}</small></span>`;
            }).join("")}</span>
          </div>
        ` : ""}
        ${tags.length ? `
          <div class="vocab-line">
            <span class="vocab-heading">适用考试 / 词汇范围</span>
            <span class="vocab-values">${tags.map(tag => `
              <span class="vocab-tag vocab-tag-${esc(tag)}" title="${esc(TAG_NAMES[tag] || "词汇标签")}">
                <b>${esc(tag.toUpperCase())}</b>
                <span class="vocab-tag-name">${esc(TAG_NAMES[tag] || "词汇标签")}</span>
              </span>
            `).join("")}</span>
          </div>
        ` : ""}
      </section>
    `;
  }

  function renderWordStats(entry) {
    const stats = [];
    if (entry.collins !== undefined && entry.collins !== "") {
      const n = Number(entry.collins);
      const valid = Number.isFinite(n);
      const count = valid ? Math.max(0, Math.min(5, n)) : 0;
      const stars = valid ? "★".repeat(count) + "☆".repeat(5 - count) : esc(entry.collins);
      stats.push(`<div class="word-stat-card stat-collins">
        <div class="word-stat-head"><span class="word-stat-label">柯林斯星级</span><span class="word-stat-symbol">✦</span></div>
        <div class="collins-rating"><span class="collins-stars">${stars}</span>${valid ? `<b class="stat-score">${count}<small>/5</small></b>` : ""}</div>
        <div class="word-stat-note">词汇熟悉度参考</div></div>`);
    }
    if (entry.oxford !== undefined && entry.oxford !== "") {
      const yes = ["1", "true", "yes"].includes(String(entry.oxford).toLowerCase());
      stats.push(`<div class="word-stat-card ${yes ? "stat-oxford-yes" : "stat-oxford-no"}">
        <div class="word-stat-head"><span class="word-stat-label">牛津核心词汇</span><span class="word-stat-symbol">◎</span></div>
        <div class="word-stat-value"><b>${yes ? "核心词汇" : "非核心词汇"}</b><span class="stat-status">${yes ? "收录" : "未标记"}</span></div>
        <div class="word-stat-note">Oxford 核心词汇标记</div></div>`);
    }
    const formatRank = value => {
      const n = Number(value);
      return Number.isFinite(n) ? n.toLocaleString("en-US") : esc(value);
    };
    if (entry.bnc !== undefined && entry.bnc !== "") {
      stats.push(`<div class="word-stat-card stat-bnc">
        <div class="word-stat-head"><span class="word-stat-label">BNC 词频排名</span><span class="word-stat-symbol">▤</span></div>
        <div class="word-stat-value"><b class="rank-number">${formatRank(entry.bnc)}</b><span class="rank-unit">名</span></div>
        <div class="word-stat-note">英国国家语料库</div></div>`);
    }
    if (entry.frq !== undefined && entry.frq !== "") {
      stats.push(`<div class="word-stat-card stat-frequency">
        <div class="word-stat-head"><span class="word-stat-label">词频排名</span><span class="word-stat-symbol">↗</span></div>
        <div class="word-stat-value"><b class="rank-number">${formatRank(entry.frq)}</b><span class="rank-unit">名</span></div>
        <div class="word-stat-note">ECDICT 综合词频</div></div>`);
    }
    if (!stats.length) return "";
    return `<section class="section"><div class="section-title">词汇等级与频率</div><div class="word-stats-panel">${stats.join("")}</div></section>`;
  }

  function renderExchange(value, baseWord, pos) {
    const forms = parseExchange(value);
    const byCode = Object.fromEntries(forms.map(x => [x.code, x.word]));
    const verbCodes = ["p", "d", "i"];
    const hasVerbForms = verbCodes.some(code => byCode[code]);
    const availableVerbCodes = ["p", "d", "i", "3"]
      .filter(code => byCode[code]);
    const showVerbNotice = isVerbEntry(pos) || availableVerbCodes.length > 0;
    const irregularCodes = getIrregularForms(baseWord, byCode);
    const doubledCodes = getDoubledVerbForms(baseWord, byCode);
    const coreCodes = ["p", "d"].filter(code => byCode[code]);
    const irregularCoreCodes = coreCodes.filter(code =>
      irregularCodes.includes(code)
    );
    const otherCodes = ["i", "3"].filter(code => byCode[code]);
    const irregularOtherCodes = otherCodes.filter(code =>
      irregularCodes.includes(code)
    );

    let changeStatus = "regular";
    if (!byCode.p || !byCode.d || !String(baseWord ?? "").trim()) {
      changeStatus = "missing";
    } else if (irregularCoreCodes.length) {
      changeStatus = coreCodes.length === 2 && irregularCoreCodes.length === 1
        ? "mixed"
        : "irregular";
    } else if (irregularOtherCodes.length) {
      changeStatus = "mixed";
    }

    const statusLabels = {
      regular: "规则变化",
      irregular: "不规则变化",
      mixed: "混合变化",
      missing: "缺少数据"
    };
    const statusDetails = {
      regular: "已提供的形式符合常见规则",
      irregular: "已提供的形式包含特殊变化",
      mixed: "部分已提供形式符合规则，部分为特殊变化",
      missing: "数据库未提供足够的动词词形，无法判断"
    };
    const labels = {
      s: "复数", "3": "第三人称单数",
      r: "比较级", t: "最高级", "0": "原形", "1": "其他词形"
    };

    const rows = [];
    if (showVerbNotice) {
      rows.push(`
        <div class="irregular-notice notice-${changeStatus}">
          <strong>${statusLabels[changeStatus]}</strong>
          <span>${statusDetails[changeStatus]}</span>
        </div>
      `);
    }

    if (hasVerbForms) {
      const columns = [
        ["", "原形", baseWord || "—"],
        ["p", "过去式", byCode.p || "—"],
        ["d", "过去分词", byCode.d || "—"],
        ["i", "现在分词", byCode.i || "—"]
      ];
      rows.push(`
        <div class="verb-forms">
          ${columns.map(([code, label, word], i) => {
            const irregular = irregularCodes.includes(code);
            const doubled = !irregular && doubledCodes.includes(code);
            return `
              ${i ? `<span class="verb-separator">→</span>` : ""}
              <div class="verb-form${irregular ? " irregular-form" : doubled ? " doubled-form" : ""}">
                <span>${label}</span>
                <b>${esc(word)}</b>
                ${irregular ? "<small>特殊变化</small>" : doubled ? "<small>双写</small>" : ""}
              </div>
            `;
          }).join("")}
        </div>
      `);
    }

    const grouped = new Map();
    for (const item of forms) {
      if (verbCodes.includes(item.code)) continue;
      const label = labels[item.code] || `其他变化 (${item.code})`;
      if (!grouped.has(label)) grouped.set(label, []);
      if (!grouped.get(label).some(x => x.word === item.word)) {
        grouped.get(label).push(item);
      }
    }

    for (const [label, items] of grouped) {
      rows.push(`
        <div class="inflection-row">
          <span class="inflection-label">${esc(label)}</span>
          <span class="inflection-words">${items.map(item => {
            const irregular = item.code === "3" &&
              irregularCodes.includes("3");
            const doubled = !irregular && item.code === "3" &&
              doubledCodes.includes("3");
            return `
              <span class="inflection-word${irregular ? " irregular-form" : doubled ? " doubled-form" : ""}">
                ${esc(item.word)}${irregular ? "<small>特殊变化</small>" : doubled ? "<small>双写</small>" : ""}
              </span>
            `;
          }).join("、")}</span>
        </div>
      `);
    }

    if (!rows.length) return "";
    return `
      <section class="section">
        <div class="section-title">词形变化</div>
        <div class="exchange-content">${rows.join("")}</div>
      </section>
    `;
  }

    // 判断音频地址是否有效

  function getAudioUrl(value) {

    if (!value || typeof value !== "string") return null;



    const source = value.trim();



    try {

      const url = new URL(source, window.location.href);



      if (url.protocol !== "http:" && url.protocol !== "https:") {

        return null;

      }



      return url.href;

    } catch {

      return null;

    }

  }



  // 使用浏览器英文语音合成



function speakWord(word) {

  if (!("speechSynthesis" in window)) {

    status("当前浏览器不支持语音合成");

    return;

  }



  const synth = window.speechSynthesis;

  synth.cancel();



  // 不强制指定 voice，让 Edge 自行选择系统语音

  const utterance = new SpeechSynthesisUtterance(word);

  utterance.lang = "en-US";

  utterance.rate = 0.85;

  utterance.volume = 1;



  let finished = false;



  utterance.onstart = () => {

    status(`正在朗读：${word}`);

  };



  utterance.onend = () => {

    finished = true;

    status("发音播放完成");

  };



  utterance.onerror = event => {

    if (finished) return;

    console.error("语音合成失败：", event);

    status(`语音合成失败：${event.error}`);

  };



  // 从用户点击事件直接启动播放

  synth.speak(utterance);

}





  // 优先播放词典音频，失败后回退到语音合成

  async function playPronunciation(entry) {

    const word = entry.word || "";

    const button = $("#speak");



    if (!word || !button) return;



    button.disabled = true;

    button.textContent = "⏳ 正在播放";



    if (state.audio) {

      state.audio.pause();

      state.audio = null;

    }



    if ("speechSynthesis" in window) {

      speechSynthesis.cancel();

    }



    const audioUrl = getAudioUrl(entry.audio);



    if (audioUrl) {

      try {

        const audio = new Audio(audioUrl);

        state.audio = audio;



        audio.addEventListener("ended", () => {

          button.disabled = false;

          button.textContent = "🔊 发音";

          status("发音播放完成");

        }, { once: true });



        audio.addEventListener("error", () => {

          state.audio = null;

        }, { once: true });



        status("正在播放词典音频…");



        await audio.play();



        button.disabled = false;

        button.textContent = "🔊 发音";

        return;

      } catch (e) {

        console.warn("词典音频播放失败，尝试语音合成：", e);

        state.audio = null;

      }

    }



    button.disabled = false;

    button.textContent = "🔊 发音";



    speakWord(word);

  }



  function renderComparisons(items) {
    if (!Array.isArray(items) || !items.length) return "";
    return '<section class="section comparison-section">' +
      '<div class="section-title">词语辨析</div><div class="comparison-list">' +
      items.map(item =>
        '<article class="comparison-item"><div class="comparison-words">' +
        esc(item.title || (item.words || []).join(", ")) +
        '</div><div class="comparison-content">' + esc(item.content || "") +
        '</div></article>'
      ).join("") + '</div></section>';
  }

  function renderWord(entry) {
    const word = entry.word || "";
    const raw = Object.entries(entry).filter(
      ([key, v]) => key !== "comparisons" && v !== null && v !== undefined && v !== ""
    );
    const phonetic = entry.phonetic || entry.phonetic_us || entry.phonetic_uk || "";

    $("#welcome").hidden = true;
    $("#empty").hidden = true;

    $("#results").innerHTML = `
      <article class="entry">
        <div class="entry-head">
          <div>
            <h2 class="word">${esc(word)}</h2>
            <div class="sub">
              ${phonetic
                ? `<span>/${esc(String(phonetic).replace(/^/|/$/g, ""))}/</span>`
                : ""}
              <button class="sound" id="speak">🔊 发音</button>
            </div>
          </div>
          <div class="book-save-actions">
            <button class="save ${isInBook("read", word) ? "saved" : ""}" data-book-save="read">${isInBook("read", word) ? "✓ 已在读本" : "＋ 读单词本"}</button>
            <button class="save ${isInBook("write", word) ? "saved" : ""}" data-book-save="write">${isInBook("write", word) ? "✓ 已在写本" : "＋ 写单词本"}</button>
          </div>
        </div>

        ${renderTranslation(entry.translation)}
        ${renderDefinitions(entry.definition)}
        ${renderComparisons(entry.comparisons)}
        ${renderVocabulary(entry)}
        ${renderWordStats(entry)}
        ${renderExchange(entry.exchange, word, entry.pos)}

        <details class="raw">
          <summary>查看完整原始词条（${raw.length} 个字段）</summary>
          <div class="raw-grid">
            ${raw.map(([k, v]) => `
              <div class="raw-field">
                <b>${esc(fieldLabel(k))}</b>
                <span>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>
              </div>
            `).join("")}
          </div>
        </details>
      </article>
    `;

    document.querySelectorAll("[data-book-save]").forEach(button => button.addEventListener("click", () => toggleBookWord(button.dataset.bookSave, word)));
    $("#speak").addEventListener("click", () => playPronunciation(entry));
    addHistory(word);
  }

    async function showWord(word) {

    hideSuggestions();



    q.value = word;

    $("#clear").style.display = "block";

    status("正在查询词条…");



    try {

      const entry = await api(

        `/api/word/${encodeURIComponent(word)}`

      );



      renderWord(entry);

      status("本地查询完成");



      state.page = "lookup";

      setSide("lookup");

    } catch (e) {

      $("#results").innerHTML = "";

      $("#welcome").hidden = true;

      $("#empty").hidden = false;



      $("#empty h2").textContent = "没有找到词条";

      $("#empty p").textContent = e.message;



      status("查询未找到");

    }

  }



  function setSide(page) {

    document.querySelectorAll(".side-btn[data-page]")

      .forEach(b => {

        b.classList.toggle("active", b.dataset.page === page);

      });

  }



  function normalizeOcrWord(value) {
    const raw = String(value || "").trim().replace(/^\d+[.)、]\s*/, "");
    const match = raw.match(/[A-Za-z][A-Za-z'-]*/);
    return match ? match[0].toLowerCase() : raw.replace(/[^A-Za-z'-]/g, "").toLowerCase();
  }

  function showImageImport() {
    state.page = "image-import";
    setSide("image-import");
    hideSuggestions();
    $("#welcome").hidden = true;
    $("#empty").hidden = true;
    status("图片单词本导入工具");

    $("#results").innerHTML = `
      <section class="image-import-panel">
        <header class="image-import-header">
          <div>
            <div class="collection-kicker">IMAGE → VOCABULARY</div>
            <h2>图片批量导入</h2>
            <p>上传手写单词表，识别英文单词与中文释义，校对后一次性加入读单词本。</p>
          </div>
          <div class="image-import-badge" id="imageEngineBadge">PaddleOCR CPU</div>
        </header>

        <div class="image-import-body">
          <label class="image-dropzone" id="imageDropzone">
            <input type="file" id="wordImage" accept="image/*" multiple hidden>
            <span class="image-drop-icon">＋</span>
            <strong>选择多张单词表图片</strong>
            <span>也可以将多张图片拖到这里 · JPG / PNG / WebP · 最多 10 张</span>
            <small>优先使用本机 PaddleOCR CPU 识别；低置信度内容再交给 Gemma 4 辅助</small>
          </label>
          <div class="image-vision-status" id="imageVisionStatus">正在检查本机 PaddleOCR 与 Gemma 4…</div>

          <div class="image-import-preview" id="imagePreview" hidden>
            <div class="image-preview-head">
              <div><b id="imageFileName">已选择图片</b><span id="imageFileSize"></span></div>
              <button type="button" class="image-text-button" id="replaceImage">更换图片</button>
            </div>
            <div class="image-preview-list" id="imagePreviewList"></div>
            <button type="button" class="image-recognize-button" id="recognizeImage">识别所选图片</button>
          </div>

          <div class="image-import-progress" id="ocrProgress" hidden>
            <div class="image-progress-top"><b id="ocrProgressTitle">正在准备 OCR 引擎…</b><span id="ocrProgressPercent">0%</span></div>
            <div class="image-progress-track"><i id="ocrProgressBar"></i></div>
            <p id="ocrProgressDetail">图片会由词典服务器逐张处理：PaddleOCR 优先识别，必要时调用本机 Gemma 4 辅助。</p>
          </div>

          <div class="image-import-note">
            <b>识别说明</b>
            <span>优先由 PaddleOCR 识别中英文；低置信度、手写或复杂排版内容再由本机 Gemma 4 辅助。识别出的英文会使用 ECDICT 词形数据转换为原型（例如 went → go），并保留图片原词形供核对。每张图片可设置独立日期段，识别词条按图片阅读顺序均匀分配日期；校对表还可逐词修改日期。原始图片会保存到词典服务器，图片中文释义仅用于核对，不会覆盖词典原有释义。</span>
          </div>

          <details class="image-manual-entry">
            <summary>识别不了？手动粘贴或录入</summary>
            <p>每行一个单词；也可使用“英文单词 + Tab + 中文释义”的格式。</p>
            <textarea id="manualWordLines" rows="5" placeholder="itchy&#9;adj. 发痒的&#10;allergy&#9;n. 过敏&#10;misery&#9;n. 苦难"></textarea>
            <button type="button" class="image-text-button" id="parseManualLines">整理为待导入列表</button>
          </details>
        </div>

        <section class="image-review" id="imageReview" hidden>
          <div class="image-review-head">
            <div>
              <h3>双语智能校对与人工确认</h3>
              <p id="imageReviewSummary">已识别 0 行</p>
              <div class="image-correction-status" id="imageCorrectionStatus">等待词典双向校对…</div>
            </div>
            <div class="image-review-tools">
              <button type="button" class="image-text-button" id="rerunImageCorrection">重新双语校对</button>
              <button type="button" class="image-text-button" id="addImportRow">＋ 添加一行</button>
              <button type="button" class="image-text-button" id="selectAllImportRows">全选</button>
            </div>
          </div>
          <div class="image-review-table-wrap">
            <table class="image-review-table">
              <thead><tr><th>导入</th><th>英文单词 / 词典建议</th><th>添加日期</th><th>图片中的中文释义</th><th>双语词典校对</th><th></th></tr></thead>
              <tbody id="imageReviewRows"></tbody>
            </table>
          </div>
          <div class="image-review-footer">
            <span id="imageSelectedCount">已选择 0 个单词</span>
            <label class="image-book-target">目标单词本
              <select id="imageBookMode"><option value="read">读单词本</option><option value="write">写单词本</option></select>
            </label>
            <button type="button" class="image-import-submit" id="importSelectedWords" disabled>导入所选单词</button>
          </div>
        </section>
      </section>
    `;

    let selectedFiles = [];
    let previewUrls = [];
    let imageDateRanges = [];
    let rows = [];
    let workerBusy = false;

    const fileInput = $("#wordImage");
    const dropzone = $("#imageDropzone");
    const preview = $("#imagePreview");
    const imagePreviewList = $("#imagePreviewList");
    const progress = $("#ocrProgress");
    const review = $("#imageReview");
    const tbody = $("#imageReviewRows");

    async function checkVisionStatus() {
      const badge = $("#imageEngineBadge");
      const info = $("#imageVisionStatus");
      try {
        const result = await api("/api/image-import/status");
        if (result.vision_available) {
          badge.textContent = `Gemma 4 · ${result.model || "多模态整图识别"}`;
          badge.classList.remove("unavailable");
          info.textContent = `Gemma 4 主识别引擎已就绪。每张图片会完整交给视觉模型识别；PaddleOCR 不参与词条拆分或配对。`;
        } else {
          badge.textContent = "Gemma 4 未就绪";
          badge.classList.add("unavailable");
          info.textContent = result.message || "Gemma 4 主识别引擎不可用，请检查本机 Ollama 和模型。";
        }
      } catch (error) {
        badge.textContent = "OCR 状态不可用";
        badge.classList.add("unavailable");
        info.textContent = `无法检查 OCR 服务：${error.message}`;
      }
    }
    checkVisionStatus();

    function progressUpdate(title, percent, detail) {
      $("#ocrProgressTitle").textContent = title;
      $("#ocrProgressPercent").textContent = `${Math.round(percent)}%`;
      $("#ocrProgressBar").style.width = `${Math.max(0, Math.min(100, percent))}%`;
      $("#ocrProgressDetail").textContent = detail || "";
    }

    function setImages(files) {
      const selected = Array.from(files || []).filter(Boolean);
      if (!selected.length) return;
      if (selected.length > 10) {
        status("一次最多选择 10 张图片");
        return;
      }
      if (selected.some(file => !file.type.startsWith("image/"))) {
        status("所选文件中包含非图片文件，请重新选择");
        return;
      }
      if (selected.some(file => file.size > 20 * 1024 * 1024)) {
        status("单张图片不能超过 20 MB");
        return;
      }
      const totalSize = selected.reduce((sum, file) => sum + file.size, 0);
      if (totalSize > 100 * 1024 * 1024) {
        status("所选图片总大小不能超过 100 MB");
        return;
      }
      selectedFiles = selected;
      previewUrls.forEach(url => URL.revokeObjectURL(url));
      previewUrls = selectedFiles.map(file => URL.createObjectURL(file));
      const today = localDateInputValue();
      imageDateRanges = selectedFiles.map(() => ({ start: today, end: today, rangeEnabled: false }));
      imagePreviewList.innerHTML = selectedFiles.map((file, index) => `
        <div class="image-preview-item" data-image-index="${index}">
          <img src="${previewUrls[index]}" alt="图片 ${index + 1}">
          <span class="image-preview-filename" title="${esc(file.name)}">${esc(file.name)}</span>
          <small>${(file.size / 1024 / 1024).toFixed(2)} MB</small>
          <label class="image-date-label">单词添加日期</label>
          <div class="image-date-single">
            <input type="date" data-image-date="single" data-index="${index}" value="${today}" aria-label="图片 ${index + 1} 单词添加日期">
            <button type="button" class="image-date-toggle" data-date-range-toggle="${index}">设置日期段</button>
          </div>
          <div class="image-date-range" data-date-range-fields="${index}" hidden>
            <input type="date" data-image-date="start" data-index="${index}" value="${today}" aria-label="图片 ${index + 1} 开始日期">
            <span>至</span>
            <input type="date" data-image-date="end" data-index="${index}" value="${today}" aria-label="图片 ${index + 1} 结束日期">
            <button type="button" class="image-date-toggle" data-date-single-toggle="${index}">使用单日</button>
          </div>
        </div>
      `).join("");
      imagePreviewList.querySelectorAll("[data-date-range-toggle]").forEach(button => button.addEventListener("click", () => {
        const index = Number(button.dataset.dateRangeToggle);
        const range = imageDateRanges[index];
        range.rangeEnabled = true;
        const single = imagePreviewList.querySelector(`[data-image-date="single"][data-index="${index}"]`);
        range.start = single.value || today;
        range.end = range.start;
        imagePreviewList.querySelector(`[data-date-range-fields="${index}"]`).hidden = false;
        single.closest(".image-date-single").hidden = true;
      }));
      imagePreviewList.querySelectorAll("[data-date-single-toggle]").forEach(button => button.addEventListener("click", () => {
        const index = Number(button.dataset.dateSingleToggle);
        const range = imageDateRanges[index];
        range.rangeEnabled = false;
        const single = imagePreviewList.querySelector(`[data-image-date="single"][data-index="${index}"]`);
        single.value = range.start;
        range.end = range.start;
        imagePreviewList.querySelector(`[data-date-range-fields="${index}"]`).hidden = true;
        single.closest(".image-date-single").hidden = false;
      }));
      imagePreviewList.querySelectorAll("[data-image-date]").forEach(input => input.addEventListener("change", () => {
        const index = Number(input.dataset.index);
        const field = input.dataset.imageDate;
        const range = imageDateRanges[index];
        if (field === "single") {
          range.start = input.value || today;
          range.end = range.start;
          return;
        }
        range[field] = input.value || today;
        if (range.end < range.start) {
          if (field === "start") range.end = range.start;
          else range.start = range.end;
          imagePreviewList.querySelector(`[data-image-date="start"][data-index="${index}"]`).value = range.start;
          imagePreviewList.querySelector(`[data-image-date="end"][data-index="${index}"]`).value = range.end;
        }
      }));
      $("#imageFileName").textContent = `已选择 ${selectedFiles.length} 张图片`;
      $("#imageFileSize").textContent = `${(totalSize / 1024 / 1024).toFixed(2)} MB`;
      preview.hidden = false;
      dropzone.hidden = true;
      review.hidden = true;
      progress.hidden = true;
      rows = [];
    }

    function correctionLevelLabel(level) {
      return ({
        verified: "英文命中",
        meaning_check: "释义待核对",
        high: "双语高度吻合",
        review: "候选待确认",
        low: "匹配较弱",
        none: "未找到候选"
      })[level] || "等待校对";
    }

    function correctionPercent(value) {
      return `${Math.round((Number(value) || 0) * 100)}%`;
    }

    function correctionMarkup(row, index) {
      const correction = row.correction;
      const visionWarning = row.recognitionUncertain
        ? `<div class="image-ai-uncertain"><b>识别结果需要核对</b>${row.recognitionNote ? `<small>${esc(row.recognitionNote)}</small>` : "<small>请重点核对这一行的原图内容。</small>"}</div>` : "";
      if (!correction) {
        return `${visionWarning}<div class="image-correction-box pending"><span class="image-correction-badge">等待双语校对</span><small>将分别检索英文词头和中文释义。</small></div>`;
      }
      const suggested = String(row.suggestionWord || correction.suggested_word || "").trim();
      const current = normalizeOcrWord(row.word);
      const stale = Boolean(row.correctionStale);
      const hasAlternative = !stale && suggested && suggested.toLowerCase() !== current.toLowerCase();
      const levelClass = correction.level === "high" || correction.level === "verified" ? "good"
        : correction.level === "review" || correction.level === "meaning_check" ? "warn" : "weak";
      const original = row.originalWord && row.originalWord.toLowerCase() !== current.toLowerCase()
        ? `<small>AI 原始识别：<s>${esc(row.originalWord)}</s></small>` : "";
      const alternative = hasAlternative
        ? `<div class="image-correction-suggestion">建议单词：<strong>${esc(suggested)}</strong></div>`
        : "";
      const dictionaryMeaning = row.dictionaryMeaning
        ? `<div class="image-correction-gloss"><b>词典释义：</b>${esc(row.dictionaryMeaning)}</div>` : `<div class="image-correction-gloss">未检索到可用词典释义</div>`;
      const metrics = `<div class="image-correction-metrics"><span>英文相似度 ${correctionPercent(correction.english_score)}</span><span>中文相似度 ${correctionPercent(correction.meaning_score)}</span><span>综合 ${correctionPercent(correction.score)}</span></div>`;
      const otherCandidates = (correction.alternatives || [])
        .filter(item => item.word && item.word.toLowerCase() !== suggested.toLowerCase() && Number(item.score) >= 0.48)
        .slice(0, 2);
      const otherCandidatesHtml = otherCandidates.length
        ? `<details class="image-correction-alternatives"><summary>其他候选（${otherCandidates.length}）</summary><div>${otherCandidates.map(item => `<button type="button" data-pick-candidate="${index}" data-word="${esc(item.word)}">${esc(item.word)} · ${correctionPercent(item.score)}</button>`).join("")}</div></details>`
        : "";
      const actions = `<div class="image-correction-actions">
        ${hasAlternative ? `<button type="button" data-apply-correction="${index}">采用建议</button>` : ""}
        ${row.autoCorrected ? `<button type="button" data-revert-correction="${index}">还原 OCR</button>` : ""}
        ${row.dictionaryMeaning && row.meaning && correction.meaning_score < 0.95 && !row.meaningFromDictionary ? `<button type="button" data-apply-meaning="${index}">采用词典释义</button>` : ""}
        ${row.meaningFromDictionary ? `<button type="button" data-revert-meaning="${index}">还原图片释义</button>` : ""}
      </div>`;
      return `${visionWarning}<div class="image-correction-box ${levelClass}">
        <span class="image-correction-badge">${stale ? "人工修改 · 待重新校对" : correctionLevelLabel(correction.level)}</span>
        ${original}${alternative}${metrics}${dictionaryMeaning}${stale ? "" : otherCandidatesHtml}
        <small class="image-correction-reason">${esc(correction.reason || "")}</small>
        ${actions}
      </div>`;
    }

    function refreshRows() {
      const validWord = row => /^[A-Za-z][A-Za-z'-]*$/.test(String(row.word || "").trim());
      const selected = rows.filter(row => row.selected && validWord(row));
      const validCount = rows.filter(validWord).length;
      const checkedCount = rows.filter(row => row.selected).length;
      const checked = rows.filter(row => row.correction && !row.correctionStale).length;
      $("#imageReviewSummary").textContent = `共 ${rows.length} 行 · ${validCount} 个有效单词 · ${checked}/${rows.length} 行完成校对 · ${checkedCount} 行已勾选`;
      $("#imageSelectedCount").textContent = `已选择 ${selected.length} 个有效单词`;
      $("#importSelectedWords").disabled = !selected.length || workerBusy || correctionBusy;
      tbody.innerHTML = rows.map((row, index) => `
        <tr data-row="${index}">
          <td><input type="checkbox" class="import-row-check" data-index="${index}" ${row.selected ? "checked" : ""} aria-label="选择 ${esc(row.word)}"></td>
          <td>
            <div class="import-word-cell">
              ${row.lemmaChanged && row.rawWord ? `<small>图片原词形：${esc(row.rawWord)} → 已转换为原型</small>` : row.rawWord && row.rawWord.toLowerCase() !== String(row.word).toLowerCase() ? `<small>图片原词形：${esc(row.rawWord)}</small>` : ""}
              ${row.lemmaUncertain && row.lemmaCandidates?.length ? `<div class="image-lemma-choices"><small>该词也可能作为独立词条使用，请确认：</small>${row.lemmaCandidates.map(candidate => `<button type="button" data-pick-lemma="${index}" data-lemma="${esc(candidate)}" class="${candidate.toLowerCase() === row.word.toLowerCase() ? "active" : ""}">${esc(candidate)}</button>`).join("")}</div>` : ""}
              ${row.sourceImage ? `<small>来源：${esc(row.sourceImage)}</small>` : ""}
              ${row.autoCorrected && !row.originalWord ? `<small>根据中文释义反向补全英文词头</small>` : row.originalWord && row.originalWord.toLowerCase() !== String(row.word).toLowerCase() && !row.lemmaChanged ? `<small>词典建议：${esc(row.originalWord)} → ${esc(row.word)}</small>` : ""}
              <input class="import-word-input" data-field="word" data-index="${index}" value="${esc(row.word)}" spellcheck="false" placeholder="英文单词">
            </div>
          </td>
          <td>
            <input type="date" class="import-date-input" data-field="studyDate" data-index="${index}" value="${esc(row.studyDate || localDateInputValue())}" aria-label="${esc(row.word)} 添加日期">
            ${(row.studyDateCandidates || []).length > 1 ? `<small class="import-date-note">多张图片重复出现，默认取最早日期</small>` : ""}
          </td>
          <td><input class="import-meaning-input" data-field="meaning" data-index="${index}" value="${esc(row.meaning)}" placeholder="中文释义"></td>
          <td>${correctionMarkup(row, index)}</td>
          <td><button type="button" class="import-row-delete" data-delete="${index}" title="删除此行" aria-label="删除第 ${index + 1} 行">×</button></td>
        </tr>
      `).join("");
      tbody.querySelectorAll(".import-row-check").forEach(input => input.addEventListener("change", () => {
        rows[Number(input.dataset.index)].selected = input.checked;
        refreshRows();
      }));
      tbody.querySelectorAll(".import-word-input, .import-meaning-input, .import-date-input").forEach(input => input.addEventListener("input", () => {
        const row = rows[Number(input.dataset.index)];
        row[input.dataset.field] = input.value;
        row.userEdited = true;
        if (row.correction) row.correctionStale = true;
        const wordInput = tbody.querySelector(`.import-word-input[data-index="${input.dataset.index}"]`);
        if (wordInput) wordInput.classList.toggle("invalid", !validWord(row));
        const validSelected = rows.filter(item => item.selected && validWord(item)).length;
        $("#imageSelectedCount").textContent = `已选择 ${validSelected} 个有效单词`;
        $("#importSelectedWords").disabled = !validSelected || workerBusy || correctionBusy;
      }));
      tbody.querySelectorAll("[data-delete]").forEach(button => button.addEventListener("click", () => {
        rows.splice(Number(button.dataset.delete), 1);
        refreshRows();
      }));
      tbody.querySelectorAll("[data-apply-correction]").forEach(button => button.addEventListener("click", () => {
        const row = rows[Number(button.dataset.applyCorrection)];
        if (!row?.suggestionWord) return;
        if (!row.originalWord) row.originalWord = row.word;
        row.word = row.suggestionWord;
        row.autoCorrected = true;
        row.userEdited = false;
        refreshRows();
      }));
      tbody.querySelectorAll("[data-revert-correction]").forEach(button => button.addEventListener("click", () => {
        const row = rows[Number(button.dataset.revertCorrection)];
        if (!row?.originalWord) return;
        row.word = row.originalWord;
        row.autoCorrected = false;
        row.userEdited = true;
        refreshRows();
      }));
      tbody.querySelectorAll("[data-apply-meaning]").forEach(button => button.addEventListener("click", () => {
        const row = rows[Number(button.dataset.applyMeaning)];
        if (!row?.dictionaryMeaning) return;
        if (row.originalMeaning === undefined) row.originalMeaning = row.meaning;
        row.meaning = row.dictionaryMeaning;
        row.meaningFromDictionary = true;
        refreshRows();
      }));
      tbody.querySelectorAll("[data-revert-meaning]").forEach(button => button.addEventListener("click", () => {
        const row = rows[Number(button.dataset.revertMeaning)];
        if (!row || row.originalMeaning === undefined) return;
        row.meaning = row.originalMeaning;
        row.meaningFromDictionary = false;
        refreshRows();
      }));
      tbody.querySelectorAll("[data-pick-candidate]").forEach(button => button.addEventListener("click", () => {
        const row = rows[Number(button.dataset.pickCandidate)];
        const candidate = button.dataset.word || "";
        if (!row || !candidate) return;
        if (row.originalWord === undefined) row.originalWord = row.word;
        row.word = candidate;
        row.suggestionWord = candidate;
        row.autoCorrected = true;
        row.userEdited = false;
        refreshRows();
      }));
      tbody.querySelectorAll("[data-pick-lemma]").forEach(button => button.addEventListener("click", () => {
        const row = rows[Number(button.dataset.pickLemma)];
        const candidate = button.dataset.lemma || "";
        if (!row || !candidate) return;
        row.word = candidate;
        row.lemmaUncertain = false;
        row.lemmaChanged = candidate.toLowerCase() !== String(row.rawWord || "").toLowerCase();
        row.userEdited = true;
        row.correctionStale = Boolean(row.correction);
        refreshRows();
      }));
    }

    let correctionBusy = false;

    async function runBilingualCorrection() {
      if (!rows.length || correctionBusy) return;
      correctionBusy = true;
      const snapshot = rows.map(row => ({ word: row.word, meaning: row.meaning }));
      const batchSize = 100; // 与后端单次请求上限一致；前端自动拆分大批次。
      const batchCount = Math.ceil(snapshot.length / batchSize);
      $("#imageCorrectionStatus").textContent = `正在进行中英文双向检索…共 ${snapshot.length} 行，将分 ${batchCount} 批处理。`;
      refreshRows();
      let fixed = 0;
      const failedBatches = [];
      try {
        for (let start = 0; start < snapshot.length; start += batchSize) {
          const end = Math.min(start + batchSize, snapshot.length);
          const batch = snapshot.slice(start, end);
          $("#imageCorrectionStatus").textContent = `正在校对第 ${Math.floor(start / batchSize) + 1}/${batchCount} 批：${start + 1}–${end} / ${snapshot.length} 行…`;
          try {
            const data = await api("/api/image-import/correct", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ items: batch })
            });
            (data.results || []).forEach((result, batchIndex) => {
              const index = start + batchIndex;
              const row = rows[index];
              const original = snapshot[index];
              if (!row || !original) return;
              // Do not overwrite a value the user edited while the request was running.
              if (row.word !== original.word || row.meaning !== original.meaning) return;
              row.originalWord = row.originalWord ?? original.word;
              row.originalMeaning = row.originalMeaning ?? original.meaning;
              row.suggestionWord = result.suggested_word || "";
              row.dictionaryMeaning = result.dictionary_meaning || "";
              row.correction = result;
              row.correctionStale = false;
              if (result.auto_correct && result.suggested_word) {
                row.word = result.suggested_word;
                row.autoCorrected = true;
                fixed += 1;
              }
            });
          } catch (error) {
            failedBatches.push({ start: start + 1, end, message: error.message });
          }
        }
        const done = rows.filter(row => row.correction).length;
        if (failedBatches.length) {
          const failedText = failedBatches.map(item => `${item.start}–${item.end} 行`).join("、");
          $("#imageCorrectionStatus").textContent = `部分校对完成：${done}/${rows.length} 行已核对，${failedText}失败。可重新校对未完成项目或手动修改。`;
          status(`双语校对部分完成：${done}/${rows.length} 行；失败批次：${failedText}`);
        } else {
          $("#imageCorrectionStatus").textContent = `双向检索完成：${done}/${rows.length} 行已核对，自动修复 ${fixed} 处；请检查建议、词典释义和原图。`;
          status(`双语校对完成：核对 ${done} 行，建议修复 ${fixed} 处`);
        }
      } catch (error) {
        $("#imageCorrectionStatus").textContent = `双语校对暂不可用：${error.message}。仍可手动修改并导入。`;
        status(`双语校对失败：${error.message}`);
      } finally {
        correctionBusy = false;
        refreshRows();
      }
    }

    async function setRows(nextRows) {
      let normalized = [];
      const wordsToNormalize = nextRows.map(item => String(item.raw_word || item.word || ""));
      try {
        const response = await api("/api/image-import/normalize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ words: wordsToNormalize })
        });
        normalized = response.items || [];
      } catch (error) {
        console.warn("词形还原接口暂不可用，保留识别原词：", error);
      }
      rows = nextRows.map((item, index) => {
        const rawWord = String(item.raw_word || item.word || "").trim();
        const lemma = normalized[index] || null;
        const word = normalizeOcrWord(item.word || lemma?.word || rawWord);
        const meaning = String(item.meaning || "").trim();
        const lemmaChanged = Boolean(item.lemma_changed || lemma?.lemma_changed);
        return {
          word: lemma?.word && !item.lemma_changed ? normalizeOcrWord(lemma.word) : word,
          meaning,
          originalWord: normalizeOcrWord(item.lemma_changed ? (item.word || rawWord) : (lemma?.word || item.word || rawWord)),
          rawWord: rawWord || String(item.raw_word || item.word || ""),
          lemmaChanged,
          lemmaCandidates: item.lemma_candidates || lemma?.lemma_candidates || [],
          lemmaUncertain: Boolean(item.lemma_uncertain || lemma?.lemma_uncertain),
          sourceImage: item.source_image || "",
          sourceImageIds: item.source_image_ids || (item.source_image_id ? [item.source_image_id] : []),
          studyDate: item.study_date || localDateInputValue(),
          studyDateCandidates: item.study_dates || [],
          originalMeaning: meaning,
          recognitionUncertain: Boolean(item.uncertain),
          recognitionNote: String(item.note || ""),
          selected: true,
          autoCorrected: false,
          userEdited: false
        };
      }).filter(item => item.word || item.meaning);
      review.hidden = false;
      refreshRows();
      review.scrollIntoView({ behavior: "smooth", block: "start" });
      runBilingualCorrection();
    }

    fileInput.addEventListener("change", () => { setImages(fileInput.files); fileInput.value = ""; });
    $("#replaceImage").addEventListener("click", () => fileInput.click());
    ["dragenter", "dragover"].forEach(type => dropzone.addEventListener(type, event => {
      event.preventDefault(); dropzone.classList.add("dragging");
    }));
    ["dragleave", "drop"].forEach(type => dropzone.addEventListener(type, event => {
      event.preventDefault(); dropzone.classList.remove("dragging");
    }));
    dropzone.addEventListener("drop", event => setImages(event.dataTransfer?.files));

    function uploadImagesForServerOCR(formData) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/image-import/recognize");
        xhr.upload.addEventListener("progress", event => {
          if (event.lengthComputable) {
            const percent = 8 + (event.loaded / event.total) * 27;
            progressUpdate("正在上传图片到词典服务器…", percent, `已上传 ${(event.loaded / event.total * 100).toFixed(0)}%`);
          }
        });
        xhr.upload.addEventListener("load", () => {
          progressUpdate("图片已上传，正在逐张识别…", 42, "PaddleOCR 优先处理；只有识别结果置信度不足时才调用 Gemma 4。首次加载 OCR 模型可能需要稍候。");
        });
        xhr.addEventListener("load", () => {
          let data = {};
          try { data = JSON.parse(xhr.responseText || "{}"); } catch {}
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(data);
          } else {
            reject(new Error(data.detail || `服务器返回 HTTP ${xhr.status}`));
          }
        });
        xhr.addEventListener("error", () => reject(new Error("无法连接词典服务器，请确认 FastAPI 正在运行。")));
        xhr.addEventListener("abort", () => reject(new Error("图片识别请求已取消。")));
        xhr.send(formData);
      });
    }

    $("#recognizeImage").addEventListener("click", async () => {
      if (!selectedFiles.length || workerBusy) return;
      workerBusy = true;
      progress.hidden = false;
      review.hidden = true;
      $("#recognizeImage").disabled = true;
      progressUpdate("正在准备批量上传…", 4, `即将上传 ${selectedFiles.length} 张图片到词典服务器，由本机 PaddleOCR / Ollama 处理；不会发送给第三方 AI 服务。`);
      try {
        const formData = new FormData();
        selectedFiles.forEach(file => formData.append("files", file, file.name || "vocabulary-image.jpg"));
        formData.append("date_ranges", JSON.stringify(imageDateRanges));
        const data = await uploadImagesForServerOCR(formData);
        const parsed = Array.isArray(data.rows) ? data.rows : [];
        const failed = (data.images || []).filter(item => !item.ok);
        (data.images || []).forEach((item, index) => {
          if (!item.image_id) return;
          const card = imagePreviewList.querySelector(`[data-image-index="${index}"]`);
          if (!card || card.querySelector("[data-original-image-link]")) return;
          const link = document.createElement("a");
          link.href = `/api/image-import/images/${encodeURIComponent(item.image_id)}`;
          link.target = "_blank";
          link.rel = "noopener";
          link.dataset.originalImageLink = "1";
          link.className = "image-original-link";
          link.textContent = "原图已保存 · 查看";
          card.appendChild(link);
        });
        const perImage = (data.images || []).map(item => `${item.filename}: ${item.saved ? "原图已保存；" : ""}${item.ok ? `识别 ${item.row_count} 行` : item.error}`).join("；");
        progressUpdate("批量识别完成", 100, `成功 ${data.success_count ?? 0}/${data.image_count ?? selectedFiles.length} 张；原图保存 ${data.saved_image_count ?? 0} 张；合并去重后 ${data.row_count ?? parsed.length} 个词条，其中 ${data.uncertain_count ?? 0} 个需要重点核对。${failed.length ? `识别失败：${failed.map(item => item.filename).join("、")}` : ""} ${perImage}`);
        if (!parsed.length) throw new Error(failed.map(item => `${item.filename}: ${item.error}`).join("；") || "没有识别到有效单词，请换用更清晰的图片。");
        await setRows(parsed);
        status(`批量识别完成：${parsed.length} 个原型词条待双语校对`);
      } catch (error) {
        progressUpdate("识别未完成", 100, error.message || "请尝试重新识别或使用手动录入。");
        status(`图片识别失败：${error.message}`);
        console.error(error);
      } finally {
        workerBusy = false;
        $("#recognizeImage").disabled = false;
        if (!review.hidden) refreshRows();
      }
    });

    $("#parseManualLines").addEventListener("click", () => {
      const text = $("#manualWordLines").value;
      const parsed = text.split(/\r?\n/).map(line => {
        const parts = line.split(/\t|\s{2,}/, 2);
        return { word: parts[0] || "", meaning: parts[1] || "" };
      }).filter(item => item.word.trim() || item.meaning.trim());
      if (!parsed.length) {
        status("请先粘贴或输入单词，每行一个");
        return;
      }
      setRows(parsed);
      status(`已整理 ${parsed.length} 行，检查后即可导入`);
    });

    $("#addImportRow").addEventListener("click", () => {
      rows.push({ word: "", meaning: "", originalWord: "", originalMeaning: "", studyDate: localDateInputValue(), studyDateCandidates: [], selected: true, autoCorrected: false });
      refreshRows();
      const input = tbody.querySelector(`.import-word-input[data-index="${rows.length - 1}"]`);
      input?.focus();
    });

    $("#rerunImageCorrection").addEventListener("click", () => {
      rows.forEach(row => { row.correction = null; row.correctionStale = false; row.suggestionWord = ""; row.dictionaryMeaning = ""; });
      runBilingualCorrection();
    });

    $("#selectAllImportRows").addEventListener("click", () => {
      const allSelected = rows.length && rows.every(row => row.selected);
      rows.forEach(row => { row.selected = !allSelected; });
      $("#selectAllImportRows").textContent = allSelected ? "全选" : "取消全选";
      refreshRows();
    });

    $("#importSelectedWords").addEventListener("click", async () => {
      const selected = rows.filter(row => row.selected && /^[A-Za-z][A-Za-z'-]*$/.test(row.word.trim()));
      if (!selected.length) return;
      const uniqueItems = new Map();
      selected.forEach(row => {
        const word = row.word.trim();
        const key = word.toLowerCase();
        const studyDate = row.studyDate || localDateInputValue();
        if (!uniqueItems.has(key)) uniqueItems.set(key, { word, study_date: studyDate });
        else if (studyDate < uniqueItems.get(key).study_date) uniqueItems.get(key).study_date = studyDate;
      });
      const items = [...uniqueItems.values()];
      const words = items.map(item => item.word);
      const invalidCount = rows.filter(row => row.selected && row.word.trim() && !/^[A-Za-z][A-Za-z'-]*$/.test(row.word.trim())).length;
      if (invalidCount && !window.confirm(`${invalidCount} 行英文单词格式不正确，将跳过这些行。继续导入 ${words.length} 个有效单词吗？`)) return;
      const button = $("#importSelectedWords");
      button.disabled = true;
      button.textContent = "正在导入…";
      try {
        const targetBook = $("#imageBookMode").value;
        const result = await api(`/api/books/${targetBook}/bulk`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items })
        });
        await loadBook(targetBook);
        const importedSet = new Set(words.map(word => word.toLowerCase()));
        rows.forEach(row => {
          if (importedSet.has(row.word.trim().toLowerCase())) row.selected = false;
        });
        refreshRows();
        status(`导入到${targetBook === "read" ? "读" : "写"}单词本：新增 ${result.created} 个，重复跳过 ${result.skipped} 个`);
        progress.hidden = false;
        progressUpdate("导入完成", 100, `新增 ${result.created} 个单词；重复或已存在 ${result.skipped} 个。`);
      } catch (error) {
        status(`导入失败：${error.message}`);
        console.error(error);
      } finally {
        button.textContent = "加入生词本";
        button.disabled = false;
        refreshRows();
      }
    });

    loadFavorites().catch(error => status(`生词本状态加载失败：${error.message}`));
  }

  async function showList(page) {
    state.page = page;
    setSide(page);
    hideSuggestions();
    $("#welcome").hidden = true;
    $("#empty").hidden = true;

    if (page === "favorites") {
      try {
        await loadFavorites();
      } catch (e) {
        $("#results").innerHTML = `
          <section class="collection-panel">
            <div class="collection-empty">
              <div class="collection-empty-icon">!</div>
              <h2>生词本暂时无法加载</h2>
              <p>${esc(e.message)}。请确认词典服务器正常运行后重试。</p>
              <button type="button" class="collection-retry" id="retryFavorites">重新加载</button>
            </div>
          </section>`;
        $("#retryFavorites").addEventListener("click", () => showList("favorites"));
        status("生词本加载失败");
        return;
      }
    }

    const isFavorites = page === "favorites";
    const total = isFavorites ? state.favorites.length : state.history.length;
    const title = isFavorites ? "我的生词本" : "最近查询";
    const subtitle = isFavorites
      ? "集中管理收藏单词。收藏内容保存在服务器，可在局域网设备间共享。"
      : "按最近查询顺序排列。查询历史保存在当前浏览器中。";

    $("#results").innerHTML = `
      <section class="collection-panel" data-page="${page}">
        <header class="collection-header">
          <div class="collection-heading">
            <div class="collection-kicker">${isFavorites ? "MY VOCABULARY" : "SEARCH HISTORY"}</div>
            <h2>${title}</h2>
            <p>${subtitle}</p>
          </div>
          <div class="collection-total">
            <strong id="collectionTotal">${total}</strong>
            <span>个单词</span>
          </div>
        </header>

        <div class="collection-toolbar">
          <label class="collection-search">
            <span aria-hidden="true">⌕</span>
            <input id="collectionSearch" type="search" placeholder="筛选单词… 按 / 快速定位" autocomplete="off" spellcheck="false">
            <kbd>/</kbd>
          </label>
          <label class="collection-sort">
            <span>排序</span>
            <select id="collectionSort">
              <option value="recent">${isFavorites ? "最近添加" : "最近查询"}</option>
              <option value="az">A → Z</option>
              <option value="za">Z → A</option>
            </select>
          </label>
          ${!isFavorites && total
            ? `<button type="button" class="collection-clear" id="clearHistory">清空历史</button>`
            : ""}
        </div>

        <div class="collection-summary">
          <span id="collectionSummary">显示 ${total} / ${total} 个单词</span>
          <span>${isFavorites ? "点击单词查看释义 · ☆ 可移除收藏" : "点击单词查看释义 · ☆ 可加入生词本"}</span>
        </div>

        <div class="collection-grid" id="collectionGrid"></div>
        <div class="collection-empty collection-no-results" id="collectionEmpty" hidden>
          <div class="collection-empty-icon">⌕</div>
          <h3 id="collectionEmptyTitle">没有匹配的单词</h3>
          <p id="collectionEmptyText">试试其他拼写。</p>
          <button type="button" id="resetCollectionSearch" class="collection-retry">清除筛选</button>
        </div>
      </section>
    `;

    const search = $("#collectionSearch");
    const sort = $("#collectionSort");
    const grid = $("#collectionGrid");
    const empty = $("#collectionEmpty");

    function getItems() {
      const source = isFavorites
        ? state.favorites.map(word => ({
            word,
            study_date: state.favoriteDates[word.toLowerCase()] || ""
          }))
        : state.history.map((word, index) => ({ word, added_at: "", position: index + 1 }));

      const needle = search.value.trim().toLocaleLowerCase();
      const items = source.filter(item => item.word.toLocaleLowerCase().includes(needle));

      if (sort.value === "az") {
        items.sort((a, b) => a.word.localeCompare(b.word, "en", { sensitivity: "base" }));
      } else if (sort.value === "za") {
        items.sort((a, b) => b.word.localeCompare(a.word, "en", { sensitivity: "base" }));
      }
      return { source, items };
    }

    function renderRows() {
      const { source, items } = getItems();
      $("#collectionTotal").textContent = source.length;
      $("#collectionSummary").textContent = `显示 ${items.length} / ${source.length} 个单词`;
      grid.innerHTML = items.map((item, index) => {
        const saved = isSaved(item.word);
        const meta = isFavorites
          ? `添加日期 ${esc(formatFavoriteDate(item.study_date))}`
          : `最近查询 · 第 ${item.position} 条`;
        const action = isFavorites
          ? `<button type="button" class="collection-action remove" data-action="remove" data-word="${esc(item.word)}" aria-label="从生词本移除 ${esc(item.word)}" title="移除收藏">移除</button>`
          : `<button type="button" class="collection-action ${saved ? "is-saved" : "add"}" data-action="add" data-word="${esc(item.word)}" ${saved ? "disabled" : ""} aria-label="${saved ? "已收藏" : `收藏 ${esc(item.word)}`}" title="${saved ? "已在生词本中" : "加入生词本"}">${saved ? "✓ 已收藏" : "☆ 收藏"}</button>`;

        return `
          <article class="collection-word-card">
            <button type="button" class="collection-word-open" data-action="open" data-word="${esc(item.word)}">
              <span class="collection-word-main">
                <span class="collection-word">${esc(item.word)}</span>
                <span class="collection-word-index">${String(index + 1).padStart(2, "0")}</span>
              </span>
              <span class="collection-word-meta">${meta}</span>
            </button>
            ${action}
          </article>`;
      }).join("");

      if (!items.length) {
        empty.hidden = false;
        grid.hidden = true;
        if (!source.length) {
          $("#collectionEmptyTitle").textContent = isFavorites ? "生词本还是空的" : "还没有查询记录";
          $("#collectionEmptyText").textContent = isFavorites
            ? "查词后，点击词条上的“加入生词本”，就能把单词收集到这里。"
            : "在上方查找一个单词，系统会自动把它加入最近查询。";
          $("#resetCollectionSearch").hidden = true;
        } else {
          $("#collectionEmptyTitle").textContent = "没有匹配的单词";
          $("#collectionEmptyText").textContent = "试试更短的关键词，或清除筛选条件。";
          $("#resetCollectionSearch").hidden = false;
        }
      } else {
        empty.hidden = true;
        grid.hidden = false;
      }

      grid.querySelectorAll("[data-action]").forEach(button => {
        button.addEventListener("click", async () => {
          const word = button.dataset.word;
          const actionName = button.dataset.action;
          if (actionName === "open") {
            showWord(word);
            return;
          }

          button.disabled = true;
          try {
            if (actionName === "remove") {
              await api(`/api/favorites/${encodeURIComponent(word)}`, { method: "DELETE" });
              await loadFavorites();
              status(`已从生词本移除 ${word}`);
            } else if (actionName === "add") {
              await api("/api/favorites", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ word })
              });
              await loadFavorites();
              status(`已收藏 ${word}`);
            }
            renderRows();
          } catch (e) {
            button.disabled = false;
            status(`操作失败：${e.message}`);
            console.error(e);
          }
        });
      });
    }

    search.addEventListener("input", renderRows);
    sort.addEventListener("change", renderRows);
    $("#resetCollectionSearch").addEventListener("click", () => {
      search.value = "";
      renderRows();
      search.focus();
    });

    if (!isFavorites && $("#clearHistory")) {
      $("#clearHistory").addEventListener("click", () => {
        if (!state.history.length) return;
        if (!window.confirm(`确定清空全部 ${state.history.length} 条最近查询记录吗？此操作不会删除生词本。`)) return;
        state.history = [];
        save("lexicon-history", state.history);
        renderRows();
        status("最近查询记录已清空");
      });
    }

    search.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        const first = grid.querySelector('[data-action="open"]');
        if (first) first.click();
      } else if (event.key === "Escape") {
        search.value = "";
        renderRows();
      }
    });

    renderRows();
    search.focus({ preventScroll: true });
  }

  document.addEventListener("keydown", event => {
    if (!["favorites", "history", "read-book", "write-book"].includes(state.page)) return;
    const target = event.target;
    if (target && (target.matches("input, textarea, select") || target.isContentEditable)) return;
    if (event.key === "/") {
      const listSearch = $("#collectionSearch");
      if (listSearch) {
        event.preventDefault();
        listSearch.focus();
      }
    }
  });


  q.addEventListener("input", () => {

    clearTimeout(state.timer);

    state.timer = setTimeout(doSuggest, 120);

  });



  q.addEventListener("keydown", e => {

    const opts = [...suggestions.querySelectorAll(".suggestion")];



    if (e.key === "ArrowDown" && opts.length) {

      e.preventDefault();

      state.selected = Math.min(

        state.selected + 1,

        opts.length - 1

      );

    } else if (e.key === "ArrowUp" && opts.length) {

      e.preventDefault();

      state.selected = Math.max(0, state.selected - 1);

    } else if (e.key === "Escape") {

      hideSuggestions();

    } else if (e.key === "Enter") {

      e.preventDefault();



      const selected = opts[state.selected];



      showWord(

        selected

          ? selected.querySelector("b").textContent

          : q.value.trim()

      );

    }



    opts.forEach((o, i) => {

      o.classList.toggle("selected", i === state.selected);

    });

  });



  $("#clear").addEventListener("click", () => {

    q.value = "";

    $("#clear").style.display = "none";



    hideSuggestions();



    $("#results").innerHTML = "";

    $("#empty").hidden = true;

    $("#welcome").hidden = false;



    q.focus();

  });



  document.querySelectorAll(".filter").forEach(b => {

    b.addEventListener("click", () => {

      state.mode = b.dataset.mode;



      document.querySelectorAll(".filter").forEach(x => {

        x.classList.toggle("active", x === b);

      });



      doSuggest();

    });

  });



  document.querySelectorAll(".side-btn[data-page]").forEach(b => {

    b.addEventListener("click", () => {

      if (b.dataset.page === "lookup") {

        state.page = "lookup";

        setSide("lookup");



        $("#results").innerHTML = "";

        $("#welcome").hidden = false;

      } else if (b.dataset.page === "read-book") {

        showBook("read");

      } else if (b.dataset.page === "write-book") {

        showBook("write");

      } else if (b.dataset.page === "mastery") {
        showMastery("read");
      } else if (b.dataset.page === "read-test") {
        showReadTest();
      } else if (b.dataset.page === "write-test") {
        showWriteTest();
      } else if (b.dataset.page === "print") {
        showPrintChooser();
      } else if (b.dataset.page === "image-import") {

        showImageImport();

      } else {

        showList(b.dataset.page);

      }

    });

  });



  document.querySelectorAll(".examples button").forEach(b => {

    b.addEventListener("click", () => showWord(b.dataset.word));

  });



  $("#random").addEventListener("click", async () => {

    try {

      const d = await api("/api/random");

      showWord(d.word);

    } catch (e) {

      status(e.message);

    }

  });



  $("#theme").addEventListener("click", () => {

    document.body.classList.toggle("dark");



    localStorage.setItem(

      "lexicon-dark",

      document.body.classList.contains("dark") ? "1" : "0"

    );

  });



  if (localStorage.getItem("lexicon-dark") === "1") {

    document.body.classList.add("dark");

  }



  initializeVocabularyBooks().catch(e => {
    status(`单词本同步失败：${e.message}`);
    console.error("生词本同步失败：", e);
  });

  api("/api/stats")

    .then(d => {

      $("#total").textContent =

        Number(d.total_entries).toLocaleString();



      $("#fields").textContent = d.fields.length;



      status(

        `${Number(d.total_entries).toLocaleString()} 个词条已就绪`

      );

    })

    .catch(() => status("词库未导入或后端未启动"));



})();
