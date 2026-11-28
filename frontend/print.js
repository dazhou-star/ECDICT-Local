(() => {
  "use strict";

  const root = document.getElementById("paper");
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);

  // Keep the exact exchange parsing and irregular/doubled-form classification
  // used by the ordinary dictionary lookup page.
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
    const finalLVariant = !w.endsWith("ll") && /[^aeiou][aeiou]l$/.test(w);
    let past;
    if (w.endsWith("e")) past = [w + "d"];
    else if (/[^aeiou]y$/.test(w)) past = [w.slice(0, -1) + "ied"];
    else if (doublesFinalZ) past = [w + "zed"];
    else if (w.endsWith("c")) past = [w + "ked"];
    else {
      past = [w + "ed"];
      if (doublesFinalConsonant) past = [w + w.slice(-1) + "ed"];
      else if (cvcEnding) past.push(w + w.slice(-1) + "ed");
    }
    let ing;
    if (w === "be") ing = ["being"];
    else if (w.endsWith("ie")) ing = [w.slice(0, -2) + "ying"];
    else if (w.endsWith("e") && !w.endsWith("ee")) ing = [w.slice(0, -1) + "ing"];
    else if (doublesFinalZ) ing = [w + "zing"];
    else if (w.endsWith("c")) ing = [w + "king"];
    else {
      ing = [w + "ing"];
      if (doublesFinalConsonant) ing = [w + w.slice(-1) + "ing"];
      else if (cvcEnding) ing.push(w + w.slice(-1) + "ing");
    }
    if (finalLVariant) {
      past.push(w + "led");
      ing.push(w + "ling");
    }
    let third;
    if (doublesFinalZ) third = w + "zes";
    else if (/(s|x|z|ch|sh|o)$/.test(w)) third = w + "es";
    else if (/[^aeiou]y$/.test(w)) third = w.slice(0, -1) + "ies";
    else third = w + "s";
    return { p: past, d: past, i: ing, "3": [third] };
  }

  function getIrregularForms(baseWord, forms) {
    const expected = getRegularVerbForms(baseWord);
    if (!expected) return [];
    return ["p", "d", "i", "3"].filter(code => {
      const actual = forms[code];
      return actual && !expected[code].includes(String(actual).trim().toLowerCase());
    });
  }

  function getDoubledVerbForms(baseWord, forms) {
    const base = String(baseWord ?? "").trim().toLowerCase();
    const expected = getRegularVerbForms(base);
    if (!expected) return [];
    const last = base.slice(-1);
    const doubleForms = { p: base + last + "ed", d: base + last + "ed", i: base + last + "ing" };
    if (/[aeiou]z$/.test(base) && !base.endsWith("zz")) doubleForms["3"] = base + last + "es";
    return Object.keys(doubleForms).filter(code => {
      const actual = String(forms[code] ?? "").trim().toLowerCase();
      return actual === doubleForms[code] && expected[code].includes(actual);
    });
  }

  function renderForms(value, baseWord) {
    const parsed = parseExchange(value);
    const byCode = Object.fromEntries(parsed.map(item => [item.code, item.word]));
    const irregular = getIrregularForms(baseWord, byCode);
    const doubled = getDoubledVerbForms(baseWord, byCode);
    const labels = { p: "过去式", d: "过去分词", i: "现在分词", "3": "第三人称单数" };
    const codes = ["p", "d", "i", "3"].filter(code =>
      byCode[code] && (irregular.includes(code) || doubled.includes(code))
    );
    if (!codes.length) return "";
    return '<div class="forms">' + codes.map(code => {
      const special = irregular.includes(code);
      return '<span><b>' + labels[code] + '</b> ' + esc(byCode[code]) +
        '<small>' + (special ? "不规则" : "双写") + '</small></span>';
    }).join("") + "</div>";
  }

  function render(data) {
    let html = '<header class="paper-head"><h1>每周复习纸带</h1><p>' +
      esc(data.week_start) + " — " + esc(data.week_end) +
      '</p><p class="sub">周日—周五 · 周六补做</p></header>';
    let questionNo = 0;
    const answers = [];

    data.days.forEach(day => {
      html += '<section class="day"><h2>' + esc(day.label) + "　" + esc(day.date) + "</h2>";
      if (!day.words.length && !day.exercises.length) {
        html += '<p class="empty">当天没有安排复习</p></section>';
        return;
      }
      if (day.words.length) {
        html += '<h3>复习单词（' + day.words.length + '）</h3>';
        day.words.forEach((word, index) => {
          html += '<article class="word"><h4>' + (index + 1) + ". " + esc(word.word) + "</h4>";
          if (word.phonetic) html += '<p class="phonetic">' + esc(word.phonetic) + "</p>";
          if (word.pos) html += '<p class="pos">' + esc(word.pos) + "</p>";
          html += '<p class="meaning">' + esc(word.translation || "暂无中文释义") + "</p>";
          html += renderForms(word.exchange, word.word) + "</article>";
        });
      }
      if (day.exercises.length) {
        html += '<h3>检测练习（' + day.exercises.length + '题）</h3>';
        day.exercises.forEach(question => {
          questionNo++;
          html += '<article class="question"><b>' + questionNo + ". " + esc(question.word) + "</b><ol type=\"A\">";
          question.options.forEach(option => { html += "<li>" + esc(option) + "</li>"; });
          html += '</ol><p class="answer-box">作答： □ A　□ B　□ C　□ D</p></article>';
          answers.push("<p>" + questionNo + ". " + esc(day.label) + " · " +
            esc(question.word) + "：" + "ABCD"[question.answer] + " — " +
            esc(question.meaning) + "</p>");
        });
      }
      html += "</section>";
    });

    html += '<section class="answers"><h2>本周答案区</h2>' +
      (answers.join("") || "<p>本周暂无练习题</p>") +
      '</section><footer>— LEXICON · END —</footer>';
    root.innerHTML = html;
    document.title = "复习纸带 " + data.week_start + " 至 " + data.week_end;
  }

  fetch("/api/print/data", { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error("服务器返回 " + response.status);
      return response.json();
    })
    .then(render)
    .catch(error => {
      root.innerHTML = '<p class="error">纸带生成失败：' + esc(error.message) + "</p>";
    });
})();
