

(() => {

  const $ = s => document.querySelector(s);



  const state = {

    mode: "prefix",

    page: "lookup",

    timer: null,

    selected: -1,

    favorites: [],
    favoriteDates: {},

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
      items.map(item => [item.word.toLowerCase(), item.added_at])
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
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
    return date.toLocaleDateString(undefined, {
      year: "numeric", month: "2-digit", day: "2-digit"
    });
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

          <small>${esc(item.translation || item.definition || "查看词条")}</small>

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



  function renderWord(entry) {
    const word = entry.word || "";
    const raw = Object.entries(entry).filter(
      ([, v]) => v !== null && v !== undefined && v !== ""
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
          <button class="save ${isSaved(word) ? "saved" : ""}" id="save">
            ${isSaved(word) ? "★ 已收藏" : "☆ 加入生词本"}
          </button>
        </div>

        ${renderTranslation(entry.translation)}
        ${renderDefinitions(entry.definition)}
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

    $("#save").addEventListener("click", () => toggleSave(word));
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
            <p>上传手写单词表，识别英文单词与中文释义，校对后一次性加入生词本。</p>
          </div>
          <div class="image-import-badge" id="imageEngineBadge">Ollama · Gemma 4</div>
        </header>

        <div class="image-import-body">
          <label class="image-dropzone" id="imageDropzone">
            <input type="file" id="wordImage" accept="image/*" hidden>
            <span class="image-drop-icon">＋</span>
            <strong>选择单词表图片</strong>
            <span>也可以将图片拖到这里 · JPG / PNG / WebP</span>
            <small>图片将上传到词典服务器，由本机 Ollama · Gemma 4 读取</small>
          </label>
          <div class="image-vision-status" id="imageVisionStatus">正在检查本机 Ollama 与 Gemma 4 模型…</div>

          <div class="image-import-preview" id="imagePreview" hidden>
            <div class="image-preview-head">
              <div><b id="imageFileName">已选择图片</b><span id="imageFileSize"></span></div>
              <button type="button" class="image-text-button" id="replaceImage">更换图片</button>
            </div>
            <img id="imagePreviewImg" alt="待识别的单词表图片">
            <button type="button" class="image-recognize-button" id="recognizeImage">使用 Gemma 4 识别图片</button>
          </div>

          <div class="image-import-progress" id="ocrProgress" hidden>
            <div class="image-progress-top"><b id="ocrProgressTitle">正在连接本机 Ollama…</b><span id="ocrProgressPercent">0%</span></div>
            <div class="image-progress-track"><i id="ocrProgressBar"></i></div>
            <p id="ocrProgressDetail">图片会由运行词典的电脑通过本机 Ollama 和 Gemma 4 进行识别。</p>
          </div>

          <div class="image-import-note">
            <b>识别说明</b>
            <span>Gemma 4 会读取整张图片并按纸面行顺序提取中英文，严格保留原始释义，不主动纠正拼写或补充内容。识别结果随后会通过本地 ECDICT 做英文拼写与中文释义双向校对。被模型标记为不确定的行会特别提示；最终请人工检查。加入生词本的内容是英文单词，图片中文释义仅用于核对，不会覆盖词典原有释义。</span>
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
              <thead><tr><th>导入</th><th>英文单词 / 词典建议</th><th>图片中的中文释义</th><th>双语词典校对</th><th></th></tr></thead>
              <tbody id="imageReviewRows"></tbody>
            </table>
          </div>
          <div class="image-review-footer">
            <span id="imageSelectedCount">已选择 0 个单词</span>
            <button type="button" class="image-import-submit" id="importSelectedWords" disabled>加入生词本</button>
          </div>
        </section>
      </section>
    `;

    let selectedFile = null;
    let previewUrl = null;
    let rows = [];
    let workerBusy = false;

    const fileInput = $("#wordImage");
    const dropzone = $("#imageDropzone");
    const preview = $("#imagePreview");
    const imagePreview = $("#imagePreviewImg");
    const progress = $("#ocrProgress");
    const review = $("#imageReview");
    const tbody = $("#imageReviewRows");

    async function checkVisionStatus() {
      const badge = $("#imageEngineBadge");
      const info = $("#imageVisionStatus");
      try {
        const result = await api("/api/image-import/status");
        if (result.available) {
          badge.textContent = `Ollama · ${result.model || "Gemma 4"}`;
          badge.classList.remove("unavailable");
          info.textContent = `已连接本机 Ollama，模型 ${result.model} 已就绪。图片将在服务器本机处理。`;
        } else {
          badge.textContent = "Gemma 4 未就绪";
          badge.classList.add("unavailable");
          info.textContent = result.message || "未检测到可用的 Gemma 4 模型。";
        }
      } catch (error) {
        badge.textContent = "Ollama 未连接";
        badge.classList.add("unavailable");
        info.textContent = `无法检查 Ollama：${error.message}`;
      }
    }
    checkVisionStatus();

    function progressUpdate(title, percent, detail) {
      $("#ocrProgressTitle").textContent = title;
      $("#ocrProgressPercent").textContent = `${Math.round(percent)}%`;
      $("#ocrProgressBar").style.width = `${Math.max(0, Math.min(100, percent))}%`;
      $("#ocrProgressDetail").textContent = detail || "";
    }

    function setImage(file) {
      if (!file) return;
      if (!file.type.startsWith("image/")) {
        status("请选择图片文件");
        return;
      }
      if (file.size > 20 * 1024 * 1024) {
        status("图片不能超过 20 MB");
        return;
      }
      selectedFile = file;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      imagePreview.src = previewUrl;
      $("#imageFileName").textContent = file.name;
      $("#imageFileSize").textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB`;
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
        ? `<div class="image-ai-uncertain"><b>Gemma 4 标记为不确定</b>${row.recognitionNote ? `<small>${esc(row.recognitionNote)}</small>` : "<small>请重点核对这一行的原图内容。</small>"}</div>` : "";
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
              ${row.autoCorrected && !row.originalWord ? `<small>根据中文释义反向补全英文词头</small>` : row.originalWord && row.originalWord.toLowerCase() !== String(row.word).toLowerCase() ? `<small>AI 原始识别：${esc(row.originalWord)} ${row.autoCorrected ? "→ 词典建议" : ""}</small>` : ""}
              <input class="import-word-input" data-field="word" data-index="${index}" value="${esc(row.word)}" spellcheck="false" placeholder="英文单词">
            </div>
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
      tbody.querySelectorAll(".import-word-input, .import-meaning-input").forEach(input => input.addEventListener("input", () => {
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
    }

    let correctionBusy = false;

    async function runBilingualCorrection() {
      if (!rows.length || correctionBusy) return;
      correctionBusy = true;
      const snapshot = rows.map(row => ({ word: row.word, meaning: row.meaning }));
      $("#imageCorrectionStatus").textContent = "正在进行中英文双向模糊检索…";
      refreshRows();
      try {
        const data = await api("/api/image-import/correct", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: snapshot })
        });
        let fixed = 0;
        (data.results || []).forEach((result, index) => {
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
        const done = rows.filter(row => row.correction).length;
        $("#imageCorrectionStatus").textContent = `双向检索完成：${done} 行已核对，自动修复 ${fixed} 处；请检查建议、词典释义和原图。`;
        status(`双语校对完成：核对 ${done} 行，建议修复 ${fixed} 处`);
      } catch (error) {
        $("#imageCorrectionStatus").textContent = `双语校对暂不可用：${error.message}。仍可手动修改并导入。`;
        status(`双语校对失败：${error.message}`);
      } finally {
        correctionBusy = false;
        refreshRows();
      }
    }

    function setRows(nextRows) {
      rows = nextRows.map(item => {
        const word = normalizeOcrWord(item.word);
        const meaning = String(item.meaning || "").trim();
        return {
          word,
          meaning,
          originalWord: word,
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

    fileInput.addEventListener("change", () => { setImage(fileInput.files?.[0]); fileInput.value = ""; });
    $("#replaceImage").addEventListener("click", () => fileInput.click());
    ["dragenter", "dragover"].forEach(type => dropzone.addEventListener(type, event => {
      event.preventDefault(); dropzone.classList.add("dragging");
    }));
    ["dragleave", "drop"].forEach(type => dropzone.addEventListener(type, event => {
      event.preventDefault(); dropzone.classList.remove("dragging");
    }));
    dropzone.addEventListener("drop", event => setImage(event.dataTransfer?.files?.[0]));

    function uploadImageForServerOCR(formData) {
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
          progressUpdate("图片已上传，Gemma 4 正在读取整张表格…", 42, "正在等待本机 Ollama 完成视觉理解和结构化转录。首次调用可能需要加载模型，请稍候。");
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
      if (!selectedFile || workerBusy) return;
      workerBusy = true;
      progress.hidden = false;
      review.hidden = true;
      $("#recognizeImage").disabled = true;
      progressUpdate("正在准备上传…", 4, "图片将上传到运行词典的电脑，由本机 Ollama + Gemma 4 处理；不会发送给第三方 AI 服务。");
      try {
        const formData = new FormData();
        formData.append("file", selectedFile, selectedFile.name || "vocabulary-image.jpg");
        const data = await uploadImageForServerOCR(formData);
        const parsed = Array.isArray(data.rows) ? data.rows : [];
        progressUpdate("Gemma 4 识别完成", 100, `${data.engine || "Ollama · Gemma 4"} · ${data.model || ""} · 识别 ${data.row_count ?? parsed.length} 行，其中 ${data.uncertain_count ?? 0} 行被标记为不确定。`);
        if (!parsed.length) throw new Error("Gemma 4 没有识别到有效内容，请换一张更清晰、完整且文字无遮挡的照片。");
        setRows(parsed);
        status(`Gemma 4 识别完成：${parsed.length} 行待双语校对`);
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
      rows.push({ word: "", meaning: "", originalWord: "", originalMeaning: "", selected: true, autoCorrected: false });
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
      const words = [...new Map(selected.map(row => [row.word.trim().toLowerCase(), row.word.trim()])).values()];
      const invalidCount = rows.filter(row => row.selected && row.word.trim() && !/^[A-Za-z][A-Za-z'-]*$/.test(row.word.trim())).length;
      if (invalidCount && !window.confirm(`${invalidCount} 行英文单词格式不正确，将跳过这些行。继续导入 ${words.length} 个有效单词吗？`)) return;
      const button = $("#importSelectedWords");
      button.disabled = true;
      button.textContent = "正在导入…";
      try {
        const result = await api("/api/favorites/bulk", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ words })
        });
        await loadFavorites();
        const importedSet = new Set(words.map(word => word.toLowerCase()));
        rows.forEach(row => {
          if (importedSet.has(row.word.trim().toLowerCase())) row.selected = false;
        });
        refreshRows();
        status(`导入完成：新增 ${result.created} 个，重复跳过 ${result.skipped} 个`);
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
            added_at: state.favoriteDates[word.toLowerCase()] || ""
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
          ? `添加于 ${esc(formatFavoriteDate(item.added_at))}`
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
    if (state.page !== "favorites" && state.page !== "history") return;
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



  initializeFavorites().catch(e => {
    status(`生词本同步失败：${e.message}`);
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
