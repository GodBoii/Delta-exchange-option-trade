(() => {
  "use strict";
  const runs = window.RUNS;
  const view = document.querySelector("#view");
  const title = document.querySelector("#view-title");
  const nav = document.querySelector("#navigation");
  if (!runs?.old || !runs?.new) {
    view.textContent = "The saved comparison data could not be loaded. Open the Markdown exports to read the original records.";
    return;
  }
  const escape = value => String(value ?? "Unknown").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const format = (value, decimals = 0) => typeof value === "number" ? value.toLocaleString("en-US", {maximumFractionDigits:decimals}) : escape(value);
  const percent = (oldValue, newValue) => `${newValue >= oldValue ? "+" : ""}${((newValue / oldValue - 1) * 100).toFixed(1)}%`;
  const json = value => `<pre>${escape(JSON.stringify(value, null, 2))}</pre>`;
  const disclosure = (label, content, open = false) => `<details${open ? " open" : ""}><summary>${label}</summary><div class="details-body">${content}</div></details>`;
  const header = run => `<h3><span class="run-tag ${run === runs.new ? "new" : "old"}">${run.label}</span><span>${run.date}</span></h3>`;
  const columns = render => `<div class="two-col">${[runs.old,runs.new].map(run => `<section class="run-column">${header(run)}${render(run)}</section>`).join("")}</div>`;
  const labels = {oiBase:"Open interest · BTC",oiQuote:"Open interest · quote currency",fundingPercent:"Funding rate · %",fundingIntervalHours:"Funding interval · hours",basisPercent:"Mark / index basis · %",oiChange1hPercent:"OI change over 1 hour · %",atmIvPercent:"ATM implied volatility · %",netCreditUsd:"Net credit · USD per normalized unit",spreadBps:"Spread · basis points",annualizedPercent:"Annualized volatility · %",bestBid:"Best bid",bestAsk:"Best ask",baseVolume:"Volume · BTC",quoteVolume:"Volume · USDT",bidBase:"Bid depth · BTC",askBase:"Ask depth · BTC",ivPercent:"Implied volatility · %",contractExpiryIst:"Contract expiry · IST",entryIst:"Entry time · IST",exitIst:"Exit time · IST",maximumLossUsd:"Maximum expiry loss · USD",maximumProfitUsd:"Maximum expiry profit · USD",bidDepthTop20Btc:"Displayed bid depth · BTC (legacy Top20 label)",askDepthTop20Btc:"Displayed ask depth · BTC (legacy Top20 label)"};
  const label = key => labels[key] || key.replace(/([a-z0-9])([A-Z])/g,"$1 $2").replace(/_/g," ").replace(/^./,c=>c.toUpperCase());
  function valueDisplay(key,item) {
    if (typeof item === "number" && /(?:At|Time|^start$|^end$)/.test(key) && item > 1e12) {
      return escape(new Date(item).toLocaleString("en-IN",{timeZone:"Asia/Kolkata",dateStyle:"medium",timeStyle:"medium"})+" IST");
    }
    return typeof item === "number" ? format(item,6) : escape(item);
  }
  function facts(value) {
    if (value === null || value === undefined) return `<p class="empty">Not recorded</p>`;
    if (typeof value !== "object") return `<p>${escape(value)}</p>`;
    if (Array.isArray(value)) {
      if (!value.length) return `<p class="empty">No recorded items</p>`;
      if (value.every(item => item === null || typeof item !== "object")) return `<ul class="table-list">${value.map(item=>`<li>${escape(item)}</li>`).join("")}</ul>`;
      return value.map((item,index)=>disclosure(escape(item?.name || item?.symbol || item?.expiry || item?.session || `Item ${index+1}`),facts(item))).join("");
    }
    const scalarEntries = Object.entries(value).filter(([,item]) => item === null || typeof item !== "object");
    const nestedEntries = Object.entries(value).filter(([,item]) => item !== null && typeof item === "object");
    return `<div class="fact-grid">${scalarEntries.map(([key,item]) => `<div class="fact"><span>${escape(label(key))}</span><strong>${valueDisplay(key,item)}</strong></div>`).join("")}</div>` + nestedEntries.map(([key,item]) => disclosure(escape(label(key)),facts(item))).join("");
  }
  const toolDescriptions = {
    show_available_strategy:"Fetched the enabled saved strategies and their full definitions.",
    get_btc_market_packet:"Fetched the processed Spot market packet and session comparisons.",
    calculate_exit_time:"Checked a holding period against listed expiries and session rules.",
    preview_strategy:"Resolved selected option legs, expiry, payoff, Greeks and fresh liquidity.",
    select_strategy_and_time:"Requested the final strategy proposal and schedule. This call does not place orders."
  };
  function metric(label, oldValue, newValue, note) {
    return `<article class="metric"><div class="label">${label}</div><strong>${format(oldValue)} <span class="muted">→</span> ${format(newValue)}</strong><div class="change${newValue > oldValue ? " up" : ""}">${percent(oldValue,newValue)}</div><small>${note}</small></article>`;
  }
  function bars(label, oldValue, newValue) {
    const maximum = Math.max(oldValue,newValue);
    return `<div class="bar-row"><h4>${label}</h4>${[["Older",oldValue,"old"],["Latest",newValue,""]].map(([name,value,style]) => `<div class="bar-label"><span>${name}</span><span>${format(value)}</span></div><div class="bar-track" style="margin-bottom:12px"><div class="bar-fill ${style}" style="width:${value/maximum*100}%"></div></div>`).join("")}</div>`;
  }
  function overview() {
    const old = runs.old, latest = runs.new;
    const rows = [["Initial input tokens",old.initialTokens,latest.initialTokens],["Total input tokens",old.metrics.input_tokens,latest.metrics.input_tokens],["Output tokens",old.metrics.output_tokens,latest.metrics.output_tokens],["Reasoning tokens",old.metrics.reasoning_tokens,latest.metrics.reasoning_tokens],["Main tool calls",old.tools.length,latest.tools.length],["News research calls",old.newsCalls,latest.newsCalls],["Starting text characters",old.startingChars,latest.startingChars],["Model duration",`${old.metrics.duration.toFixed(1)} s`,`${latest.metrics.duration.toFixed(1)} s`],["Provider cost",`$${old.metrics.cost.toFixed(4)}`,`$${latest.metrics.cost.toFixed(4)}`]];
    return `<div class="metrics">${metric("Main tool calls",old.tools.length,latest.tools.length,"Six fewer calls in the latest run")}${metric("Initial input tokens",old.initialTokens,latest.initialTokens,"More evidence supplied before inference")}${metric("Total input tokens",old.metrics.input_tokens,latest.metrics.input_tokens,"Across all requests in the main run")}${metric("Attached charts",old.charts.length,latest.charts.length,"Latest keeps the three price charts")}</div>
      <div class="two-col"><section class="panel"><h3>Less fetching. More evidence upfront.</h3><ol class="process"><li><span class="step-number">1</span><div><p>Older: request context through tools</p><small>Strategy catalogue + market packet, followed by five expiry checks.</small></div></li><li><span class="step-number">2</span><div><p>Latest: read curated starting input</p><small>Spot, history, options, both futures venues, liquidity and catalogue arrive together.</small></div></li><li><span class="step-number">3</span><div><p>Preview once, then select</p><small>Only two tools were used in this latest BTC run. Both final decisions are available in their own view.</small></div></li></ol><div class="note">Total input fell 8.0%, even though the first request grew 23.3%. The repeated context cost changed with the number of tool rounds.</div></section><section class="panel"><h3>Input usage</h3>${bars("First model request",old.initialTokens,latest.initialTokens)}${bars("All model requests combined",old.metrics.input_tokens,latest.metrics.input_tokens)}</section></div>
      <section class="panel" style="margin-top:20px"><h3>The complete numerical comparison</h3><div class="table-wrap"><table class="compare-table"><thead><tr><th>Measurement</th><th>Older</th><th>Latest</th><th>Change</th></tr></thead><tbody>${rows.map(([label,a,b]) => `<tr><td>${label}</td><td>${format(a)}</td><td>${format(b)}</td><td>${typeof a === "number" ? percent(a,b) : "—"}</td></tr>`).join("")}</tbody></table></div></section>
      <div class="note neutral">These runs preceded the October 1 guardrail update. Their configured limits were 40 news calls, 24 main calls and 8 compact recheck calls. The current limits of 50 / 48 / 16 are not reflected in these saved runs.</div>`;
  }
  function inputs() {
    return `<p class="tabs-info">This is the processed evidence the model received. Explore a section for its values; original input text is available at the bottom.</p>${columns(run => {
      const categories = run === runs.new ? [["Spot indicators",run.market.spot],["Sideways, volume & volatility",run.market.history],["Timeframe summaries",run.market.timeframes],["Trade flow",run.market.flow],["Liquidity",run.market.liquidity],["Futures: Binance & Delta",run.market.futures],["Option expiry overview",run.market.options],["Holding-period move scales",run.market.holdingMoveScales]] : [["Ticker",run.market.ticker],["Computed indicators",run.market.computedAnalysis],["Session history",run.market.sessionHistory],["Timeframe summaries",run.market.timeframes],["Spot order book",run.market.spotOrderBook],["Recent trade flow",run.market.recentTradeFlow]];
      return `<div class="panel"><h3>${run === runs.new ? "Supplied at the start" : "Fetched through get_btc_market_packet"}</h3><p class="tool-caption">${run === runs.new ? "The starting input already contains market evidence and saved strategies." : "The starting input contained charts, news and instructions. The model called a tool for these numerical values."}</p><div class="metrics-inline"><span>${format(run.startingChars)} starting text characters</span><span>${format(run.initialTokens)} initial tokens</span></div></div>${categories.map(([label,data]) => disclosure(escape(label),facts(data))).join("")}${disclosure("Saved strategy catalogue",facts(run.catalogue))}${disclosure("Exact starting input text",run.inputs.map(m => `<h4>${escape(m.role)}</h4><pre>${escape(m.content)}</pre>`).join(""))}`;
    })}`;
  }
  function tools() {
    return `<p class="tabs-info">Every recorded main-agent tool call, in execution order. Open a call to inspect its arguments and result.</p>${columns(run => `<div class="panel"><h3>${run.tools.length} calls</h3><div class="metrics-inline">${Object.entries(run.tools.reduce((counts,t) => ({...counts,[t.name]:(counts[t.name] || 0)+1}),{})).map(([name,count]) => `<span>${escape(name)} × ${count}</span>`).join("")}</div></div>${run.tools.map((tool,index) => {
      const result = tool.result;
      const status = result?.valid === false || result?.status === "rejected" ? "Rejected" : result?.status === "committed" ? "Committed" : "Returned";
      return disclosure(`<span class="call-num">${String(index+1).padStart(2,"0")}</span><span class="tool-name">${escape(tool.name)}</span>`, `<p class="tool-caption">${toolDescriptions[tool.name] || "Calculation on supplied numerical evidence."}</p><div class="call-meta"><span class="result-status">${status}</span><span>${format(tool.seconds,3)} seconds</span><span>${format(JSON.stringify(result).length)} result characters</span></div><h4>Arguments</h4>${facts(tool.args)}<h4>Result</h4>${facts(result)}${disclosure("Exact JSON result",json(result))}`);
    }).join("")}`)}`;
  }
  function news() {
    return `<p class="tabs-info">The actual report supplied by the news subagent. Research traces are available below each report; the full original news dialogue was not persisted.</p>${columns(run => `<div class="panel"><div class="news-stats"><span><strong>${run.newsCalls}</strong> research calls</span><span><strong>${format(run.newsChars)}</strong> report characters</span></div><article class="article">${run.newsHtml}</article></div>${disclosure(`News research trace · ${run.newsCalls} calls`,run.newsTrace.map((t,index) => disclosure(`<span class="call-num">${index+1}</span>${escape(t.name)}`,`<h4>Query / arguments</h4>${json(t.args)}<h4>Recorded result</h4>${facts(t.result)}`)).join(""))}`)}`;
  }
  function decision() {
    return `<p class="tabs-info">Final application reports, preserved as written. Different outcomes reflect different evidence and market conditions.</p>${columns(run => `<article class="panel article">${run.decisionHtml}</article>${disclosure("Run identifier and model metrics",facts({runId:run.id,model:"deepseek/deepseek-v4.1-flash",...run.metrics}))}`)}`;
  }
  function charts() {
    return `<p class="tabs-info">The original images attached to each run. Select an image to inspect it at full resolution.</p>${columns(run => run.charts.map(chart => `<figure class="chart"><a href="${escape(chart.path)}" target="_blank" rel="noopener"><img src="${escape(chart.path)}" alt="${escape(run.label+": "+chart.label)}" loading="lazy" width="1600" height="900"></a><figcaption>${escape(chart.label)}</figcaption></figure>`).join(""))}`;
  }
  function transcript() {
    return `<div class="action-row"><p class="tabs-info" style="margin:0">Public messages in their stored order. Private reasoning is excluded.</p><button id="collapse-all">Collapse all</button></div>${columns(run => `${disclosure("Starting system and user messages",run.inputs.map(m => `<h4>${escape(m.role)}</h4><pre>${escape(m.content)}</pre>`).join(""))}${run.messages.map((message,index) => disclosure(`<span class="message-label">${escape(message.role)}</span>Message ${index+1}${message.tool_calls ? " · tool request" : ""}`, `${message.tool_calls ? `<h4>Tool requests</h4>${json(message.tool_calls)}` : ""}${message.content ? `<pre>${escape(typeof message.content === "string" ? message.content : JSON.stringify(message.content,null,2))}</pre>` : `<p class="empty">No visible text; see tool requests.</p>`}`)).join("")}`)}`;
  }
  const views = {overview,inputs,tools,news,decision,charts,transcript};
  const titles = {overview:"Overview",inputs:"Starting inputs",tools:"Tool calls",news:"News analysis",decision:"Final decisions",charts:"Charts",transcript:"Full transcript"};
  function show(name) {
    if (!views[name]) name = "overview";
    title.textContent = titles[name];
    view.innerHTML = views[name]();
    nav.querySelectorAll("button").forEach(button => {
      if (button.dataset.view === name) button.setAttribute("aria-current","page");
      else button.removeAttribute("aria-current");
    });
    view.querySelectorAll(".article a").forEach(link => {link.target = "_blank";link.rel = "noopener noreferrer";});
    document.querySelector("#collapse-all")?.addEventListener("click",() => view.querySelectorAll("details").forEach(item => {item.open=false;}));
  }
  nav.addEventListener("click",event => {
    const button = event.target.closest("button[data-view]");
    if (!button) return;
    show(button.dataset.view);
    history.replaceState(null,"",`#${button.dataset.view}`);
    window.scrollTo({top:0,behavior:"instant"});
  });
  window.addEventListener("hashchange",() => show(location.hash.slice(1)));
  show(location.hash.slice(1) || "overview");
})();
