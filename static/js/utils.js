/** DOM helper utilities. */

export function $(selector) {
  return document.querySelector(selector);
}

export function $$(selector) {
  return document.querySelectorAll(selector);
}

export function show(el) {
  el.hidden = false;
}

export function hide(el) {
  el.hidden = true;
}

export function on(el, event, handler) {
  el.addEventListener(event, handler);
}

export function clearChildren(el) {
  el.innerHTML = '';
}

export function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/** Simple markdown-to-HTML (bold, italic, code, links, lists, headers). */
export function renderMarkdown(text) {
  // Pre-process: force section headers onto their own line with bold
  text = text.replace(/(^|\n)\s*(?:\*\*)?(結論|法規依據|依據|注意事項)\s*[:：]?\s*\*?\*?[ \t]*/g, '\n\n**$2:**\n');
  text = text.trim();

  let html = escapeHtml(text);

  // Code blocks (``` ... ```)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Law cross-reference markers [1]..[99]: subtle non-interactive index style
  html = html.replace(/\[(\d{1,2})\]/g, '<span class="law-xref">[$1]</span>');
  // Bold bracketed references like [法規名稱] (pure-digit refs already handled above)
  html = html.replace(/\[(?!\d{1,2}\])([^\]]+)\]/g, '<strong>[$1]</strong>');
  // Italic
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Ordered lists (1. 2. 3.)
  html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
  // Unordered lists (- or *)
  html = html.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
  // Wrap consecutive <li> groups in <ol> or <ul>
  html = html.replace(/(<li>.*<\/li>\n?)+/g, (match) => `<ul>${match}</ul>`);
  // Line breaks
  html = html.replace(/\n\n/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');
  html = '<p>' + html + '</p>';
  // Clean up empty paragraphs and unwrap block elements from <p>
  html = html.replace(/<p><\/p>/g, '');
  html = html.replace(/<p>(<h[123]>)/g, '$1');
  html = html.replace(/(<\/h[123]>)<\/p>/g, '$1');
  html = html.replace(/<p>(<ul>)/g, '$1');
  html = html.replace(/(<\/ul>)<\/p>/g, '$1');
  html = html.replace(/<p>(<pre>)/g, '$1');
  html = html.replace(/(<\/pre>)<\/p>/g, '$1');
  // Remove <br> immediately after block-level opening / before closing
  html = html.replace(/(<\/(?:ul|ol|li|h[123])>)<br>/g, '$1');
  html = html.replace(/<br>(<(?:ul|ol|li|\/ul|\/ol)>)/g, '$1');
  html = html.replace(/<br>(<p>)/g, '$1');

  // Section headers: add Gemini sparkle icon and wrap as .section-header
  html = html.replace(/<strong>(結論|法規依據|依據|注意事項)[:：]<\/strong>/g,
    '<span class="section-header"><span class="gemini-icon" aria-hidden="true">✦</span><strong>$1:</strong></span>');

  // Collapse law article quotes: <strong>《...》第N條/第N點...</strong> followed by 「...」.
  // 單位須含「點」：行政規則（處理規範/作業程序）用第N點，漏掉會造成
  // 「引到點-單位法規的答案永遠不摺疊」的偶發現象（2026-07-19 修）。
  // Header may carry a cross-reference marker (<span class="law-xref">[n]</span>) —
  // include it in the collapsed summary so the [n] stays visible when folded.
  // 相關法規（延伸參考）條目在 [n] 後還有「— 一句說明」尾巴——一併收進
  // summary（限定以破折號開頭，避免誤摺結論段行內引用；2026-07-19 加）。
  // Separator covers paragraph-break (</p><p>) and one-or-more line-breaks (<br>)
  // — 模型偶爾在標題與條文間多空一行，單一 <br> 也會漏摺。
  html = html.replace(
    /(<strong>《[^》]+》第\s*[\d一二三四五六七八九十百]+(?:\s*[-－之]\s*[\d一二三四五六七八九十百]+)?\s*[條點][^<]*<\/strong>(?:\s*<span class="law-xref">\[\d{1,2}\]<\/span>)?(?:\s*[—–─][^<]*)?)(?:\s*<\/p>\s*<p>\s*|\s*(?:<br>\s*)+)「([^」]+)」/g,
    (match, header, quote) =>
      `<details class="law-article"><summary>${header}</summary><div class="law-article-body">「${quote}」</div></details>`
  );

  // Merge consecutive collapsed blocks with the SAME article title into one block.
  // 同一條的多個項被 LLM 引成多段時，標題會連續重複（例如 §35 出現三次），
  // 讀者易誤解成三條不同法條——合併為單一標題、多段引文。
  // summary 可帶「— 說明」尾巴（相關法規格式）；backreference 要求完全相同
  // （同條＋同說明）才合併，主要/相關條目不會跨區塊誤併。
  const dupLaw = /<details class="law-article"><summary>(<strong>[^<]+<\/strong>(?:\s*<span class="law-xref">\[\d{1,2}\]<\/span>)?(?:[^<]*)?)<\/summary><div class="law-article-body">([\s\S]*?)<\/div><\/details>((?:\s|<br>|<p>|<\/p>)*?)<details class="law-article"><summary>\1<\/summary><div class="law-article-body">([\s\S]*?)<\/div><\/details>/;
  while (dupLaw.test(html)) {
    html = html.replace(dupLaw, (m, header, body1, gap, body2) =>
      `<details class="law-article"><summary>${header}</summary><div class="law-article-body">${body1}<br><br>${body2}</div></details>`
    );
  }

  return html;
}
