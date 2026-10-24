"""Claude Bot 對話頁前端狀態探針（Codex 審 12d3f62 的 9 項）。GAS JSONP 由路由攔截，依情境回應；合成資料。
用法：python3.12 page_probe.py <index.html>；印每項 PASS/FAIL。"""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

HTML = Path(sys.argv[1]).read_text(encoding="utf-8")
GAS = "https://script.google.com/macros/s/"
LIFF = "window.liff={init:()=>Promise.resolve(),isLoggedIn:()=>true,getAccessToken:()=>'tok',login:()=>{}};"
STATE = {"ok": True, "chat": [], "state": {"day": "2026-10-24", "rounds": {}}, "settings": {"slots": ["10:00"]}, "tasks": {"items": []}, "rules": []}


FASTPOLL = "(function(){var st=window.setTimeout;window.setTimeout=function(f,d){if(d===4000||d===30000)d=60;return st.call(window,f,d);};})();"


async def page_with(handler, ua=None, fast=False, init=None):
    p = await async_playwright().start()
    b = await p.chromium.launch()
    pg = await b.new_page(viewport={"width": 390, "height": 800}, **({"user_agent": ua} if ua else {}))
    if fast:
        await pg.add_init_script(FASTPOLL)   # 只縮輪詢間隔（4s／30s），api 20s 逾時不動
    if init:
        await pg.add_init_script(init)
    log = []

    async def route(r):
        u = r.request.url
        if "sdk.js" in u:
            return await r.fulfill(body=LIFF, content_type="application/javascript")
        if u.startswith("https://x.test/"):
            return await r.fulfill(body=HTML, content_type="text/html")
        if u.startswith(GAS):
            q = {k: v[0] for k, v in parse_qs(urlparse(u).query).items()}
            log.append(q)
            res = await handler(q, log)
            if res is None:   # 不回＝讓頁面自己逾時
                return await r.fulfill(body="/* hang */", content_type="application/javascript")
            if res == "ABORT":   # 連線失敗（script onerror）：與逾時同類＝收件不明
                return await r.abort()
            return await r.fulfill(body=f"{q['callback']}({json.dumps(res, ensure_ascii=False)})", content_type="application/javascript")
        return await r.abort()

    await pg.route("**/*", route)
    return p, b, pg, log


async def base(q, log, ask=None, chat=None):
    op = q.get("op")
    if op == "state":
        return dict(STATE, chat=chat if chat is not None else [])
    if op == "chat":
        return {"ok": True, "chat": chat if chat is not None else []}
    if op == "ask":
        return ask(q) if ask else {"ok": True, "chat": [{"id": "m" + q["cid"], "role": "me", "text": q["text"], "status": "pending", "at": "2026-10-24T10:00:00"}]}
    return {"ok": True}


async def t_cid_reused_after_timeout():
    n = {"ask": 0}
    async def h(q, log):
        if q.get("op") == "ask":
            n["ask"] += 1
            return None if n["ask"] == 1 else await base(q, log)   # 第一次逾時（雲端其實可能已收）
        return await base(q, log)
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.fill("#q", "第一題"); await pg.click("#send")
    await pg.wait_for_timeout(21500)   # api 逾時 20 秒
    back = pg.locator("button", has_text="放回輸入框")   # 新契約：失敗不自動回填，原樣重送＝放回再送
    if await back.count():
        await back.first.click()
    await pg.click("#send"); await pg.wait_for_timeout(800)
    cids = [x["cid"] for x in log if x.get("op") == "ask"]
    await b.close(); await p.stop()
    return len(cids) == 2 and cids[0] == cids[1], cids


async def t_failure_keeps_new_draft_and_quote():
    async def h(q, log):
        if q.get("op") == "ask":
            return {"ok": False, "msg": "busy"}
        return await base(q, log, chat=[{"id": "bx1", "role": "bot", "text": "前一則回答", "at": "2026-10-24T09:00:00"}])
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("setReply('引用的那句')")
    await pg.evaluate("Q.value='第一題'; fitQ(); SEND.onclick(); Q.value='第二題尚未送出'; Q.dispatchEvent(new Event('input'));")
    await pg.wait_for_timeout(800)
    q = await pg.input_value("#q"); reply = await pg.evaluate("REPLY")
    pq = await pg.evaluate("UNSENT.map(function(x){return x.quote})")
    await b.close(); await p.stop()
    return q == "第二題尚未送出" and (reply == "引用的那句" or pq == ["引用的那句"]), (q, reply, pq)


async def t_stale_poll_does_not_drop_new_message():
    gate = asyncio.Event()
    async def h(q, log):
        if q.get("op") == "chat":
            await gate.wait()
            return {"ok": True, "chat": []}   # 舊輪詢：還沒有新訊息
        return await base(q, log, chat=[{"id": "mold0000001", "role": "me", "text": "舊題", "status": "pending", "at": "2026-10-24T09:00:00"}])
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(5000)   # 有待回答 ⇒ 4 秒後發輪詢，卡在 gate
    await pg.fill("#q", "新題"); await pg.click("#send"); await pg.wait_for_timeout(500)
    gate.set(); await pg.wait_for_timeout(800)
    texts = await pg.evaluate("chat().map(function(x){return x.text})")
    await b.close(); await p.stop()
    return "新題" in texts, texts


async def t_teach_menu_keeps_draft():
    p, b, pg, log = await page_with(base)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.fill("#q", "我還沒送的字")
    await pg.click("#plus"); await pg.wait_for_timeout(200)
    await pg.locator("#mcard button", has_text="教它一項任務").click()
    q = await pg.input_value("#q"); sheet = await pg.get_attribute("#teach", "class")
    await b.close(); await p.stop()
    # 二版（10-24）：＋→教它一項任務＝開錄製面板；輸入框裡還沒送的字原封不動
    return sheet == "on" and q == "我還沒送的字", (sheet, q)


async def t_teach_record_flow():
    """錄製面板：開始＝op teach act start 帶「想要的結果」；狀態列跟雲端狀態走（準備→錄製中有停止→學習中沒停止）；
    停止＝op teach act stop；過時的狀態（Mac 3 分鐘沒接手）不顯示狀態列。"""
    import datetime as dt
    now = (dt.datetime.utcnow() + dt.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
    cur = {"t": None}
    async def h(q, log):
        if q.get("op") == "teach":
            cur["t"] = {"sid": "s000000000001", "state": "start" if q.get("act") == "start" else "stop", "at": now}
            return {"ok": True, "teach": cur["t"]}
        if q.get("op") == "state":
            return dict(STATE, chat=[], teach=cur["t"])
        return await base(q, log)
    fast5 = "(function(){var st=window.setTimeout;window.setTimeout=function(f,d){if(d===5000)d=60;return st.call(window,f,d);};})();"
    p, b, pg, log = await page_with(h, fast=True, init=fast5)   # 錄製中輪詢 5 秒一次：縮短才看得到狀態列跟著變
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.click("#plus"); await pg.wait_for_timeout(150)
    await pg.locator("#mcard button", has_text="教它一項任務").click()
    await pg.fill("#teachGoal", "每天祝壽星生日快樂"); await pg.click("#teachGo"); await pg.wait_for_timeout(300)
    starts = [x for x in log if x.get("op") == "teach" and x.get("act") == "start"]
    s1 = (await pg.get_attribute("#teachBar", "class"), await pg.text_content("#teachTxt"), await pg.get_attribute("#teach", "class"))
    cur["t"] = dict(cur["t"], state="recording"); await pg.wait_for_timeout(400)
    s2 = (await pg.text_content("#teachTxt"), await pg.is_visible("#teachStop"))
    await pg.click("#teachStop"); await pg.wait_for_timeout(300)
    stops = [x for x in log if x.get("op") == "teach" and x.get("act") == "stop"]
    cur["t"] = dict(cur["t"], state="learning"); await pg.wait_for_timeout(400)
    s3 = (await pg.text_content("#teachTxt"), await pg.is_visible("#teachStop"))
    # 過時的另一場（Mac 3 分鐘沒接手的 start）：不顯示狀態列。同一場不會倒退回 start（雲端轉移表只往後走），所以用新 sid
    cur["t"] = {"sid": "s000000000009", "state": "start", "at": "2020-01-01T00:00:00"}; await pg.wait_for_timeout(400)
    s4 = await pg.get_attribute("#teachBar", "class")
    await b.close(); await p.stop()
    ok = (len(starts) == 1 and starts[0].get("goal") == "每天祝壽星生日快樂" and s1[0] == "on" and "準備錄製" in s1[1] and s1[2] == ""
          and "錄製中" in s2[0] and s2[1] and len(stops) == 1 and "學習" in s3[0] and not s3[1] and s4 == "")
    return ok, (starts, s1, s2, len(stops), s3, s4)


async def t_teach_review_84b793d():
    """Codex 審 84b793d 三項：①有待回答訊息（只抓 chat）時狀態列仍跟雲端走；②按開始前出發的舊 state 回應不得蓋掉新狀態；③停止失敗不得說已停止。"""
    import datetime as dt
    now = (dt.datetime.utcnow() + dt.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
    cur = {"t": None, "stop_ok": True}
    gate = asyncio.Event(); gate.set()
    async def h(q, log):
        op = q.get("op")
        if op == "teach":
            if q.get("act") == "stop" and not cur["stop_ok"]:
                return {"ok": False, "msg": "denied", "teach": cur["t"]}
            cur["t"] = {"sid": "s000000000002", "state": "start" if q.get("act") == "start" else "stop", "at": now}
            return {"ok": True, "teach": cur["t"]}
        if op == "state":
            snap = cur["t"]
            await gate.wait()
            return dict(STATE, chat=[{"id": "mpend0000001", "role": "me", "text": "等回答", "status": "pending", "at": now}] if cur.get("pend") else [], teach=snap)
        if op == "chat":
            return {"ok": True, "chat": [{"id": "mpend0000001", "role": "me", "text": "等回答", "status": "pending", "at": now}]}
        return await base(q, log)
    fast5 = "(function(){var st=window.setTimeout;window.setTimeout=function(f,d){if(d===5000)d=60;return st.call(window,f,d);};})();"
    p, b, pg, log = await page_with(h, fast=True, init=fast5)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    # ② 舊 state 回應卡住（出發時 teach=None）→ 按開始 → 放行舊回應
    gate.clear(); await pg.wait_for_timeout(200)
    await pg.evaluate("openTeach(); 0;"); await pg.click("#teachGo"); await pg.wait_for_timeout(200)
    gate.set(); await pg.wait_for_timeout(300)
    r2 = await pg.get_attribute("#teachBar", "class")
    # ① 有待回答訊息（poll 走 chat 分支）時，雲端進入 recording，狀態列要跟上
    cur["pend"] = True; await pg.evaluate("DATA.chat = [{id:'mpend0000001',role:'me',text:'等回答',status:'pending',at:'" + now + "'}]; render(); 0;")
    cur["t"] = dict(cur["t"], state="recording"); await pg.wait_for_timeout(600)
    r1 = await pg.text_content("#teachTxt")
    # ③ 停止失敗
    cur["stop_ok"] = False
    await pg.click("#teachStop"); await pg.wait_for_timeout(200)
    r3 = await pg.text_content("#toast")
    await b.close(); await p.stop()
    return r2 == "on" and "錄製中" in (r1 or "") and "沒停成" in (r3 or ""), (r2, r1, r3)


async def t_teach_out_of_order():
    """Codex 審 479ae54 P2：同一場錄製，兩次輪詢亂序回來（learning 先到、舊的 recording 晚到）不得倒退；新的一場（不同 sid、按過開始）照收。"""
    p, b, pg, log = await page_with(base)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    r = await pg.evaluate("""(function(){
      var now = new Date(Date.now() + 8*3600e3).toISOString().slice(0,19), ep = TEACH_EP;
      DATA.teach = {sid:'s000000000003', state:'recording', at:now};
      takeTeach({sid:'s000000000003', state:'learning', at:now}, ep);
      takeTeach({sid:'s000000000003', state:'recording', at:now}, ep);     // 晚到的舊回應
      var a = DATA.teach.state;
      takeTeach(null, ep); var b = DATA.teach && DATA.teach.state;        // 舊快照 null
      takeTeach({sid:'s000000000003', state:'done', at:now}, ep - 1);     // 出發早於按鈕操作
      var c = DATA.teach.state;
      takeTeach({sid:'s000000000004', state:'start', at:now}, ep);        // 新的一場
      return [a, b, c, DATA.teach.sid];
    })()""")
    await b.close(); await p.stop()
    return r == ["learning", "learning", "learning", "s000000000004"], r


async def t_teach_stop_response_late():
    """Codex 審 b437241 P2：按停止、停止回應延遲；按下後出發的輪詢先拿到 learning；晚到的停止回應（stop）不得把狀態列蓋回「停止中」。
    走真的按鈕與輪詢（不是直接呼叫 takeTeach），舊頁面（按鈕回應直接寫 DATA.teach）跑會紅。"""
    import datetime as dt
    now = (dt.datetime.utcnow() + dt.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
    sid = "s000000000005"
    cur = {"t": {"sid": sid, "state": "recording", "at": now}, "hold": False}
    gate, hold = asyncio.Event(), asyncio.Event()
    async def h(q, log):
        op = q.get("op")
        if op == "teach" and q.get("act") == "stop":
            stop_t = dict(cur["t"], state="stop"); cur["t"] = dict(stop_t, state="learning")   # 雲端很快被 Mac 推到 learning
            await gate.wait()
            return {"ok": True, "teach": stop_t}
        if op == "state":
            if cur["hold"]:
                await hold.wait()   # 放行停止回應之後不再有輪詢來「修正」：看得到停止回應本身有沒有讓狀態倒退
            return dict(STATE, chat=[], teach=cur["t"])
        return await base(q, log)
    fast5 = "(function(){var st=window.setTimeout;window.setTimeout=function(f,d){if(d===5000)d=60;return st.call(window,f,d);};})();"
    p, b, pg, log = await page_with(h, fast=True, init=fast5)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    a = await pg.text_content("#teachTxt")
    await pg.click("#teachStop"); await pg.wait_for_timeout(500)     # 停止回應卡住；這段期間的輪詢拿到 learning
    m = await pg.text_content("#teachTxt")
    cur["hold"] = True; await pg.wait_for_timeout(200)
    gate.set(); await pg.wait_for_timeout(400)                       # 放行晚到的停止回應
    z = await pg.text_content("#teachTxt"); st = await pg.evaluate("DATA.teach && DATA.teach.state")
    hold.set(); await b.close(); await p.stop()
    return "錄製中" in (a or "") and "學習" in (m or "") and "學習" in (z or "") and st == "learning", (a, m, z, st)


async def t_layout_class_survives_quote_cancel():
    p, b, pg, log = await page_with(base)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("setAtt({url:'data:image/png;base64,iVBORw0KGgo=',b64:'iVBORw0KGgo=',mime:'image/png'}); setReply('x'); setReply(null);")
    cls = await pg.get_attribute("#barbox", "class")
    await b.close(); await p.stop()
    return "att" in (cls or "").split(), cls


async def t_upload_does_not_eat_next_image_or_quote():
    gate = asyncio.Event(); seen = {}
    async def h(q, log):
        if q.get("op") == "ask":
            seen["quote"] = q.get("quote"); return await base(q, log)
        return await base(q, log)
    p, b, pg, log = await page_with(h)
    async def up(r):
        await gate.wait()
        await r.fulfill(body=json.dumps({"ok": True, "att": "aabcdefgh12"}), content_type="application/json")
    await pg.route("https://script.google.com/macros/s/**", lambda r: up(r) if r.request.method == "POST" else r.fallback())
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("setReply('引用A'); setAtt({url:'data:image/png;base64,QQ==',b64:'QUFBQQ==',mime:'image/jpeg'}); Q.value='看圖'; fitQ(); void SEND.onclick(); 0;")
    await pg.wait_for_timeout(300)
    await pg.evaluate("setReply('引用B'); setAtt({url:'data:image/png;base64,Qg==',b64:'QkJCQg==',mime:'image/jpeg'});")
    gate.set(); await pg.wait_for_timeout(1000)
    att_b = await pg.evaluate("ATT && ATT.b64"); reply = await pg.evaluate("REPLY")
    await b.close(); await p.stop()
    return seen.get("quote") == "引用A" and att_b == "QkJCQg==" and reply == "引用B", (seen.get("quote"), att_b, reply)


async def t_fail_after_typing_keeps_draft_and_unsent_full():
    async def h(q, log):
        if q.get("op") == "ask":
            return {"ok": False, "msg": "full"}
        return await base(q, log)
    p, b, pg, log = await page_with(h)
    async def up(r): await r.fulfill(body=json.dumps({"ok": True, "att": "aabcdefgh12"}), content_type="application/json")
    await pg.route("https://script.google.com/macros/s/**", lambda r: up(r) if r.request.method == "POST" else r.fallback())
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    long = "長文" * 300
    await pg.evaluate("setReply('引用X'); setAtt({url:'data:image/png;base64,QQ==',b64:'QUFBQQ==',mime:'image/jpeg'}); Q.value=%s; fitQ(); void SEND.onclick(); setReply(null); Q.value='新打的B'; Q.dispatchEvent(new Event('input')); 0;" % json.dumps(long))
    await pg.wait_for_timeout(1500)
    st = await pg.evaluate("({q:Q.value, att:!!ATT, reply:REPLY, unsent:UNSENT.map(function(x){return x.text})})")
    await b.close(); await p.stop()
    ok = st["q"] == "新打的B" and not st["att"] and st["reply"] is None and st["unsent"] == [long]
    return ok, {k: (v if k != "unsent" else [len(x) for x in v]) for k, v in st.items()}


async def t_fail_untouched_keeps_whole_message():
    async def h(q, log):
        if q.get("op") == "ask":
            return {"ok": False, "msg": "busy"}
        return await base(q, log)
    p, b, pg, log = await page_with(h)
    async def up(r): await r.fulfill(body=json.dumps({"ok": True, "att": "aabcdefgh12"}), content_type="application/json")
    await pg.route("https://script.google.com/macros/s/**", lambda r: up(r) if r.request.method == "POST" else r.fallback())
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("setReply('引用X'); setAtt({url:'data:image/png;base64,QQ==',b64:'QUFBQQ==',mime:'image/jpeg'}); Q.value='文字A'; fitQ(); void SEND.onclick(); 0;")
    await pg.wait_for_timeout(1500)
    st = await pg.evaluate("({q:Q.value, att:ATT && ATT.b64, reply:REPLY, unsent:UNSENT.map(function(x){return [x.text, x.quote, x.attObj && x.attObj.b64]})})")
    await b.close(); await p.stop()
    return st == {"q": "", "att": None, "reply": None, "unsent": [["文字A", "引用X", "QUFBQQ=="]]}, st


async def t_type_then_clear_counts_as_touched():
    async def h(q, log):
        if q.get("op") == "ask":
            return {"ok": False, "msg": "busy"}
        return await base(q, log)
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("Q.value='舊文A'; fitQ(); void SEND.onclick(); Q.value='x'; Q.dispatchEvent(new Event('input')); Q.value=''; Q.dispatchEvent(new Event('input')); 0;")
    await pg.wait_for_timeout(1200)
    st = await pg.evaluate("({q:Q.value, unsent:UNSENT.map(function(x){return x.text})})")
    await b.close(); await p.stop()
    return st == {"q": "", "unsent": ["舊文A"]}, st


async def t_choices_show_despite_unsent():
    bot = {"id": "bq1", "role": "bot", "reply_to": "mq1", "text": "要哪個？", "choices": ["甲", "乙"], "at": "2026-10-24T10:00:00"}
    async def h(q, log):
        return await base(q, log, chat=[{"id": "mq1", "role": "me", "text": "問", "status": "answered", "at": "2026-10-24T09:59:00"}, bot])
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("UNSENT.push({id:'uzz',role:'me',text:'沒送成的',quote:'',att:'',status:'unsent',why:'x',at:'2026-10-24T10:01:00'}); render(); 0;")
    n = await pg.evaluate("document.querySelectorAll('.opts button').length")
    await b.close(); await p.stop()
    return n == 2, n


async def t_line_inapp_banner_390():
    p, b, pg, log = await page_with(base, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari Line/14.17.0")
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    href = await pg.evaluate("(document.querySelector('a[href*=openExternalBrowser]')||{}).href || null")
    w = await pg.evaluate("document.documentElement.scrollWidth")
    await b.close(); await p.stop()
    p2, b2, pg2, _ = await page_with(base)
    await pg2.goto("https://x.test/"); await pg2.wait_for_timeout(800)
    none = await pg2.evaluate("document.querySelector('a[href*=openExternalBrowser]') === null")
    await b2.close(); await p2.stop()
    return bool(href) and href.endswith("?openExternalBrowser=1") and w <= 390 and none, (href, w, none)


async def t_draft_edit_store_intact():
    async def h(q, log):
        if q.get("op") == "ask":
            return {"ok": False, "msg": "busy"}
        return await base(q, log)
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("EDITS['card1'] = '我改過的稿'; Q.value='問'; Q.dispatchEvent(new Event('input')); void SEND.onclick(); 0;")
    await pg.wait_for_timeout(1200)
    st = await pg.evaluate("({t: typeof EDITS, v: EDITS && EDITS['card1']})")
    await b.close(); await p.stop()
    return st == {"t": "object", "v": "我改過的稿"}, st


async def t_teach_during_pending_not_overwritten():
    gate = asyncio.Event()
    async def h(q, log):
        if q.get("op") == "ask":
            await gate.wait()
            return {"ok": False, "msg": "busy"}
        return await base(q, log)
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("Q.value='舊訊息'; fitQ(); void SEND.onclick(); 0;")
    await pg.click("#plus"); await pg.wait_for_timeout(150)
    await pg.locator("#mcard button", has_text="教它一項任務").click()
    gate.set(); await pg.wait_for_timeout(1200)
    st = await pg.evaluate("({q:Q.value, unsent:UNSENT.map(function(x){return x.text}), sheet:document.getElementById('teach').className})")
    await b.close(); await p.stop()
    return st["sheet"] == "on" and st["q"] == "" and st["unsent"] == ["舊訊息"], st


async def t_line_banner_visible():
    p, b, pg, log = await page_with(base, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari Line/14.17.0")
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    r = await pg.evaluate("""(function(){ var e=document.getElementById('inapp'); if(!e) return null; var b=e.getBoundingClientRect();
        var x=b.left+b.width/2, y=b.top+b.height/2, top=document.elementFromPoint(x,y); return {vis: !!top && (top===e || e.contains(top)), y: Math.round(b.top), h: Math.round(b.height)}; })()""")
    await pg.mouse.wheel(0, 2000); await pg.wait_for_timeout(300)
    r2 = await pg.evaluate("(function(){var e=document.getElementById('inapp'),b=e.getBoundingClientRect(),t=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2);return !!t&&(t===e||e.contains(t));})()")
    await b.close(); await p.stop()
    return bool(r and r["vis"] and r2), (r, r2)


# ───────── (D) 驗收矩陣 T01–T37（設計 L20261024022859-f7b518）；讀取器只用新舊兩版都有的介面（#q/#rq/#ap、ATT、UNSENT、路由紀錄）─────────
import struct, zlib
TEACH = "教你一項任務：每天 "
IA = "{url:'data:image/png;base64,QQ==',b64:'QUFBQQ==',mime:'image/jpeg'}"
IB = "{url:'data:image/png;base64,Qg==',b64:'QkJCQg==',mime:'image/jpeg'}"
A64, B64 = "QUFBQQ==", "QkJCQg=="
COMP = "(function(){var rq=document.getElementById('rq');return {text:Q.value,quote:(rq.className.indexOf('on')>=0?document.getElementById('rqt').textContent:null),img:(document.getElementById('ap').className.indexOf('on')>=0&&window.ATT?ATT.b64:null)};})()"
PEND = "UNSENT.map(function(x){return {text:x.text,quote:x.quote||'',img:(x.attObj&&x.attObj.b64)||'',state:x.state||x.status||'',tries:x.tries||0,putBack:!!x.putBack,why:x.why||''};})"
E = {"text": "", "quote": None, "img": None}


def js_compose(text="", quote=None, img=None):
    return "setReply(%s); setAtt(%s); Q.value=%s; Q.dispatchEvent(new Event('input')); " % (json.dumps(quote), img or "null", json.dumps(text))


def js_type(t):
    return "Q.value=%s; Q.dispatchEvent(new Event('input')); 0;" % json.dumps(t)


SEND_JS = "void SEND.onclick(); 0;"
A_JS = js_compose("文字a", "qA", IA)


def asks(log):
    return [x for x in log if x.get("op") == "ask"]


async def mk(handler, upload=None, **kw):
    p, b, pg, log = await page_with(handler, **kw)
    ups = []
    async def up(r):
        ups.append(json.loads(r.request.post_data or "{}").get("upload", {}))
        res = await upload(len(ups)) if upload else {"ok": True, "att": "att%d" % len(ups)}
        if res is None:
            return
        await r.fulfill(body=json.dumps(res), content_type="application/json")
    await pg.route("https://script.google.com/macros/s/**", lambda r: up(r) if r.request.method == "POST" else r.fallback())
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    return p, b, pg, log, ups


async def snap(pg):
    return await pg.evaluate(COMP), await pg.evaluate(PEND)


async def click_text(pg, t):
    loc = pg.locator("button", has_text=t)
    if await loc.count():
        await loc.first.click(); return True
    return False


async def retry_same(pg):   # 新版：待處理項目的「重試」；舊版沒有這顆鈕 ⇒ 舊版自己會回填，直接再按送出
    if not await click_text(pg, "重試"):
        await pg.evaluate(SEND_JS)


def asker(seq, gate=None):
    """seq：每次 ask 的回應（'ok'／'full'／'too long'／'ABORT'／'HANG'），用完沿用最後一個。"""
    n = {"i": 0}
    async def h(q, log):
        if q.get("op") == "ask":
            i = n["i"]; n["i"] += 1; kind = seq[min(i, len(seq) - 1)]
            if gate is not None and i == 0:
                await gate.wait()
            if kind == "ABORT": return "ABORT"
            if kind == "HANG": return None
            if kind == "ok": return await base(q, log)
            return {"ok": False, "msg": kind}
        return await base(q, log)
    return h


async def done(p, b):
    await b.close(); await p.stop()


async def t01():
    p, b, pg, log, ups = await mk(asker(["ok"]))
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(1000)
    c, u = await snap(pg); a = asks(log); await done(p, b)
    return c == E and u == [] and len(a) == 1 and (a[0]["text"], a[0]["quote"], a[0]["att"]) == ("文字a", "qA", "att1"), (c, u, len(a))


async def t02():
    p, b, pg, log, ups = await mk(asker(["full"]))
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(1000)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == E and [(x["text"], x["quote"], x["img"], x["state"]) for x in u] == [("文字a", "qA", A64, "rejected")] and n == 1, (c, u, n)


async def _upload_fail(after_js):
    gate = asyncio.Event()
    async def upf(i):
        await gate.wait(); return {"ok": False, "msg": "x"}
    p, b, pg, log, ups = await mk(asker(["ok"]), upload=upf)
    await pg.evaluate(A_JS + SEND_JS + after_js); await pg.wait_for_timeout(300)
    gate.set(); await pg.wait_for_timeout(800)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c, u, n


async def t03():
    c, u, n = await _upload_fail(js_type("b"))
    return c == {"text": "b", "quote": None, "img": None} and [(x["text"], x["img"], x["state"]) for x in u] == [("文字a", A64, "uploadFailed")] and n == 0, (c, u, n)


async def t04():
    c, u, n = await _upload_fail("setAtt(%s); 0;" % IB)
    return c == {"text": "", "quote": None, "img": B64} and [(x["text"], x["state"]) for x in u] == [("文字a", "uploadFailed")] and n == 0, (c, u, n)


async def t05():
    gate = asyncio.Event()
    async def upg(i):
        await gate.wait(); return {"ok": True, "att": "att1"}
    p, b, pg, log, ups = await mk(asker(["ok"]), upload=upg)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(300)
    await pg.evaluate(js_compose("b", "qB", IB) + "0;"); gate.set(); await pg.wait_for_timeout(1000)
    c, u = await snap(pg); a = asks(log); await done(p, b)
    return c == {"text": "b", "quote": "qB", "img": B64} and u == [] and len(a) == 1 and (a[0]["text"], a[0]["quote"]) == ("文字a", "qA"), (c, u, len(a))


async def _gated(after_js, kind="ABORT", **kw):
    gate = asyncio.Event()
    p, b, pg, log, ups = await mk(asker([kind, "ok"], gate=gate), **kw)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(300)
    if callable(after_js): await after_js(pg)
    else: await pg.evaluate(after_js)
    gate.set(); await pg.wait_for_timeout(900)
    return p, b, pg, log


async def t06():
    p, b, pg, log = await _gated("setReply('qB'); setReply(null); " + js_type("b"))
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == {"text": "b", "quote": None, "img": None} and [(x["text"], x["quote"], x["img"], x["state"]) for x in u] == [("文字a", "qA", A64, "unknown")] and n == 1, (c, u, n)


async def t07():
    p, b, pg, log = await _gated(js_type("x") + js_type(""))
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == E and [(x["text"], x["state"]) for x in u] == [("文字a", "unknown")] and n == 1, (c, u, n)


async def _teach(pg):
    await pg.click("#plus"); await pg.wait_for_timeout(150)
    await pg.locator("#mcard button", has_text="教它一項任務").click(); await pg.wait_for_timeout(100)
    await pg.click("#teachNo"); await pg.wait_for_timeout(50)   # 二版：開的是錄製面板，按取消關掉


async def t08():
    p, b, pg, log = await _gated(_teach)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == E and [(x["text"], x["state"]) for x in u] == [("文字a", "unknown")] and n == 1, (c, u, n)


async def t09():
    p, b, pg, log, ups = await mk(asker(["ok"]))
    await pg.evaluate(js_compose("b", "qB", IB) + "0;"); await _teach(pg); await _teach(pg)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == {"text": "b", "quote": "qB", "img": B64} and u == [] and n == 0, (c, u, n)


async def t10():
    long = "長" * 1900
    gate = asyncio.Event()
    p, b, pg, log, ups = await mk(asker(["full"], gate=gate), fast=True)
    await pg.evaluate(js_type(long) + SEND_JS); await pg.wait_for_timeout(200)
    await pg.evaluate(js_type("b")); gate.set(); await pg.wait_for_timeout(1200)   # 期間多次輪詢重繪
    c, u = await snap(pg)
    shown = await pg.evaluate("Array.from(document.querySelectorAll('.me div')).some(function(d){return d.textContent===%s})" % json.dumps(long))
    n = len(asks(log)); await done(p, b)
    return c["text"] == "b" and [(x["text"], x["state"]) for x in u] == [(long, "rejected")] and shown and n == 1, (c, [(len(x["text"]), x["state"]) for x in u], shown, n)


async def t11():
    p, b, pg, log = await _gated(js_type("b"), kind="full")
    await click_text(pg, "放回輸入框"); c1 = await pg.evaluate(COMP)
    await pg.evaluate(js_type("")); await click_text(pg, "放回輸入框"); await pg.wait_for_timeout(200)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c1["text"] == "b" and c == {"text": "文字a", "quote": "qA", "img": A64} and [(x["text"], x["img"], x["state"], x["putBack"]) for x in u] == [("文字a", A64, "rejected", True)] and n == 1, (c1, c, u, n)


async def _preset_rejected(**kw):
    p, b, pg, log = await _gated(js_type("x") + js_type(""), kind="full", **kw)
    return p, b, pg, log


async def t12():
    p, b, pg, log = await _preset_rejected()
    n0 = len(asks(log)); await pg.evaluate("setReply('qB'); 0;"); await click_text(pg, "放回輸入框"); await pg.wait_for_timeout(200)
    c, u = await snap(pg); n = len(asks(log)) - n0; await done(p, b)
    return c == {"text": "", "quote": "qB", "img": None} and [(x["text"], x["state"], x["putBack"]) for x in u] == [("文字a", "rejected", False)] and n == 0, (c, u, n)


async def t13():
    p, b, pg, log = await _preset_rejected()
    n0 = len(asks(log)); await pg.evaluate(js_type("   ")); await click_text(pg, "放回輸入框"); await pg.wait_for_timeout(200)
    c, u = await snap(pg); n = len(asks(log)) - n0; await done(p, b)
    return c == {"text": "   ", "quote": None, "img": None} and [(x["text"], x["state"], x["putBack"]) for x in u] == [("文字a", "rejected", False)] and n == 0, (c, u, n)


async def _twice(img1, img2):
    p, b, pg, log, ups = await mk(asker(["full"]))
    await pg.evaluate(js_compose("同文", None, img1) + SEND_JS); await pg.wait_for_timeout(700)
    await pg.evaluate(js_compose("同文", None, img2) + SEND_JS); await pg.wait_for_timeout(700)
    c, u = await snap(pg); cids = [x["cid"] for x in asks(log)]; await done(p, b)
    return c, u, cids


async def t14():
    c, u, cids = await _twice(IA, IB)
    return c == E and sorted(x["img"] for x in u) == [A64, B64] and len(cids) == 2 and cids[0] != cids[1], (c, u, cids)


async def t15():
    c, u, cids = await _twice(None, None)
    return c == E and [x["text"] for x in u] == ["同文", "同文"] and len(cids) == 2 and cids[0] != cids[1], (c, u, cids)


async def _putback_resend(seq, mod=None, **kw):
    p, b, pg, log, ups = await mk(asker(seq), **kw)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(700)
    await click_text(pg, "放回輸入框"); await pg.wait_for_timeout(100)
    if mod: await pg.evaluate(js_type(mod))
    await pg.evaluate(SEND_JS); await pg.wait_for_timeout(800)
    c, u = await snap(pg); cids = [x["cid"] for x in asks(log)]; await done(p, b)
    return c, u, cids


async def t16():
    c, u, cids = await _putback_resend(["ABORT"])
    return c == E and [(x["text"], x["state"], x["tries"]) for x in u] == [("文字a", "unknown", 2)] and len(cids) == 2 and cids[0] == cids[1], (c, u, cids)


async def t17():
    c, u, cids = await _putback_resend(["full", "ok"])
    return c == E and u == [] and len(cids) == 2 and cids[0] != cids[1], (c, u, cids)


async def t18():
    c, u, cids = await _putback_resend(["ABORT"], mod="文字a′")
    return c == {"text": "文字a′", "quote": "qA", "img": A64} and [(x["text"], x["state"]) for x in u] == [("文字a", "unknown")] and len(cids) == 1, (c, u, cids)


def cloud_has(flag):
    async def h(q, log):
        op = q.get("op"); a = asks(log)
        if op == "ask": return "ABORT"
        if op in ("chat", "state") and a and flag["on"]:
            return await base(q, log, chat=[{"id": "m" + a[0]["cid"], "role": "me", "text": a[0]["text"], "quote": a[0].get("quote", ""), "status": "pending", "at": "2026-10-24T10:00:00"}])
        return await base(q, log)
    return h


COUNT_A = "Array.from(document.querySelectorAll('.me:not(.pend) > div')).filter(function(d){return d.textContent==='文字a'}).length"


async def t19():
    p, b, pg, log, ups = await mk(cloud_has({"on": True}), fast=True)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(300)
    await pg.evaluate(js_type("b")); await pg.wait_for_timeout(1000)
    c, u = await snap(pg); k = await pg.evaluate(COUNT_A); n = len(asks(log)); await done(p, b)
    return c == {"text": "b", "quote": None, "img": None} and u == [] and k == 1 and n == 1, (c, u, k, n)


async def t20():
    flag = {"on": False}
    p, b, pg, log, ups = await mk(cloud_has(flag), fast=True)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(500)
    await click_text(pg, "放回輸入框"); flag["on"] = True; await pg.wait_for_timeout(800)
    note = await pg.evaluate("(document.getElementById('cn')||{}).textContent||''")
    await pg.evaluate(SEND_JS); await pg.wait_for_timeout(600)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == {"text": "文字a", "quote": "qA", "img": A64} and "已收到" in note and u == [] and n == 1, (c, note, u, n)


async def t21():
    gate, hang = asyncio.Event(), asyncio.Event()
    k = {"state": 0}
    async def h(q, log):
        op = q.get("op")
        if op == "state":
            k["state"] += 1
            if k["state"] == 2: await gate.wait(); return await base(q, log)   # 舊輪詢：出發時還沒有 A
            if k["state"] > 2: await hang.wait()
        return await base(q, log)
    p, b, pg, log, ups = await mk(h, fast=True)
    for _ in range(30):
        if k["state"] >= 2: break
        await pg.wait_for_timeout(50)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(500)
    gate.set(); await pg.wait_for_timeout(300)
    texts = await pg.evaluate("chat().map(function(x){return x.text})")
    c, u = await snap(pg); n = len(asks(log)); hang.set(); await done(p, b)
    return "文字a" in texts and c == E and u == [] and n == 1, (texts, c, u, n)


async def t22():
    p, b, pg, log, ups = await mk(asker(["ABORT"]), fast=True)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(1200)
    polls = sum(1 for x in log[[i for i, x in enumerate(log) if x.get("op") == "ask"][0]:] if x.get("op") in ("chat", "state"))
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == E and [(x["text"], x["state"]) for x in u] == [("文字a", "unknown")] and polls >= 2 and n == 1, (c, u, polls, n)


Q1 = {"id": "mq1", "role": "me", "text": "問", "status": "answered", "at": "2026-10-24T09:59:00"}
BQ = {"id": "bq1", "role": "bot", "reply_to": "mq1", "text": "要哪個？", "choices": ["甲", "乙"], "at": "2026-10-24T10:00:00"}
NOPTS = "document.querySelectorAll('.opts button').length"


async def t23():
    flag = {"on": False}; gate = asyncio.Event()
    async def h(q, log):
        if q.get("op") == "ask":
            await gate.wait(); return {"ok": False, "msg": "full"}
        return await base(q, log, chat=[Q1, dict(BQ, id="bq2")] if flag["on"] else [Q1])
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.evaluate(A_JS + SEND_JS + js_type("x") + js_type("")); await pg.wait_for_timeout(200)
    gate.set(); await pg.wait_for_timeout(400)
    await pg.evaluate(js_type("草稿x")); flag["on"] = True; await pg.wait_for_timeout(800)
    c, u = await snap(pg); k = await pg.evaluate(NOPTS); await done(p, b)
    return c == {"text": "草稿x", "quote": None, "img": None} and [(x["text"], x["state"]) for x in u] == [("文字a", "rejected")] and k == 2, (c, u, k)


async def t24():
    p, b, pg, log, ups = await mk(lambda q, log: base(q, log, chat=[Q1, BQ]))
    await pg.evaluate(js_compose("b", "qB", IB) + "0;")
    await pg.locator(".opts button", has_text="甲").click(); await pg.wait_for_timeout(600)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == {"text": "b", "quote": "qB", "img": B64} and u == [] and n == 0, (c, u, n)


async def t25():
    async def h(q, log):
        if q.get("op") == "ask": return {"ok": False, "msg": "full"}
        return await base(q, log, chat=[Q1, BQ])
    p, b, pg, log, ups = await mk(h)
    await pg.evaluate("var bs=document.querySelectorAll('.opts button'); bs[0].click(); bs[0].click(); 0;"); await pg.wait_for_timeout(900)
    c, u = await snap(pg); k = await pg.evaluate(NOPTS); n = len(asks(log)); await done(p, b)
    return c == E and [(x["text"], x["state"]) for x in u] == [("甲", "rejected")] and k == 2 and n == 1, (c, u, k, n)


async def t26():
    p, b, pg, log = await _gated("setReply('qA'); " + js_type("b"), kind="ok")
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == {"text": "b", "quote": "qA", "img": None} and u == [] and n == 1, (c, u, n)


async def t27():
    st = {"day": "2026-10-24", "rounds": {"10:00": {"at": "2026-10-24T10:00:00", "result": "done", "opened": [{"name": "甲群"}],
          "drafts": [{"card_id": "cardR", "chat": "甲", "head": "原話", "draft": "原稿一"}]}}}
    ch = [{"id": "bd1", "role": "bot", "text": "給你草稿", "drafts": [{"to": "乙", "text": "原稿二"}], "at": "2026-10-24T10:05:00"}]
    async def h(q, log):
        if q.get("op") == "ask": return {"ok": False, "msg": "full"}
        if q.get("op") == "state": return dict(STATE, state=st, chat=ch)
        return await base(q, log, chat=ch)
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.fill("textarea[aria-label='回覆草稿（可直接修改）']", "改稿一")
    await pg.fill("textarea[aria-label='草稿（可直接修改）']", "改稿二")
    await pg.evaluate("document.activeElement.blur(); " + js_compose("文字a") + SEND_JS); await pg.wait_for_timeout(600)
    await pg.click("#tasks"); await pg.wait_for_timeout(200); await pg.click("#back"); await pg.wait_for_timeout(600)
    v = await pg.evaluate("[document.querySelector(\"textarea[aria-label='回覆草稿（可直接修改）']\").value, document.querySelector(\"textarea[aria-label='草稿（可直接修改）']\").value, typeof EDITS]")
    c, u = await snap(pg); await done(p, b)
    return v == ["改稿一", "改稿二", "object"] and c == E and [(x["text"], x["state"]) for x in u] == [("文字a", "rejected")], (v, c, u)


async def t28():
    t = "字" * 2001
    p, b, pg, log, ups = await mk(asker(["ok"]))
    await pg.evaluate(js_type(t) + SEND_JS); await pg.wait_for_timeout(500)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c["text"] == t and u == [] and n == 0, (len(c["text"]), u, n)


async def t29():
    t = "長" * 1500
    p, b, pg, log, ups = await mk(asker(["too long"]))
    await pg.evaluate(js_type(t) + SEND_JS); await pg.wait_for_timeout(800)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c == E and [(x["text"], x["state"]) for x in u] == [(t, "rejected")] and n == 1, (c, [(len(x["text"]), x["state"]) for x in u], n)


async def t30():
    t = "（看這張圖）"
    p, b, pg, log, ups = await mk(asker(["full"]))
    await pg.evaluate(js_type(t) + SEND_JS); await pg.wait_for_timeout(700)
    await click_text(pg, "放回輸入框"); await pg.wait_for_timeout(200)
    c, u = await snap(pg); n = len(asks(log)); await done(p, b)
    return c["text"] == t and [(x["text"], x["state"], x["putBack"]) for x in u] == [(t, "rejected", True)] and n == 1, (c, u, n)


def png(w, h):
    raw = b"".join(b"\x00" + b"\x80\x20\x20" * w for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")


HOLDIMG = """(function(){var R=window.Image;window.__imgq=[];window.Image=function(){var im=new R(),h=null;
Object.defineProperty(im,'onload',{set:function(f){h=f;},get:function(){return h;}});
im.addEventListener('load',function(){window.__imgq.push({w:im.width,go:function(){h&&h.call(im);}});});return im;};})();"""


async def t31():
    p, b, pg, log, ups = await mk(asker(["ok"]), init=HOLDIMG)
    await pg.set_input_files("#file", files=[{"name": "a.png", "mimeType": "image/png", "buffer": png(2, 2)}])
    await pg.wait_for_function("window.__imgq.length>=1", timeout=5000)
    await pg.set_input_files("#file", files=[{"name": "b.png", "mimeType": "image/png", "buffer": png(3, 3)}])
    await pg.wait_for_function("window.__imgq.length>=2", timeout=5000)
    await pg.evaluate("window.__imgq.filter(function(x){return x.w===3})[0].go(); window.__imgq.filter(function(x){return x.w===2})[0].go(); 0;")
    await pg.wait_for_timeout(300)
    w = await pg.evaluate("new Promise(function(r){ if(!window.ATT) return r(null); var i=document.createElement('img'); i.onload=function(){r(i.naturalWidth)}; i.src=ATT.url; })")
    u = await pg.evaluate(PEND); n = len(asks(log)); await done(p, b)
    return w == 3 and u == [] and n == 0, (w, u, n)


async def t32():
    gate = asyncio.Event(); k = {"i": 0}
    async def h(q, log):
        if q.get("op") == "ask":
            k["i"] += 1
            if k["i"] == 1: return None   # 第一次：真的掛到頁面 20 秒逾時
            await gate.wait(); return await base(q, log)
        return await base(q, log)
    p, b, pg, log, ups = await mk(h)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(21000)
    await retry_same(pg); await pg.wait_for_timeout(300)
    locked = await pg.evaluate(js_type("y") + " SEND.disabled")
    await pg.evaluate(js_type("")); gate.set(); await pg.wait_for_timeout(800)
    c, u = await snap(pg); cids = [x["cid"] for x in asks(log)]; await done(p, b)
    return locked and c == E and u == [] and len(cids) == 2 and cids[0] == cids[1], (locked, c, u, cids)


async def t33():
    p, b, pg, log, ups = await mk(asker(["ABORT", "full"]))
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(600)
    await retry_same(pg); await pg.wait_for_timeout(700)
    c, u = await snap(pg); cids = [x["cid"] for x in asks(log)]; await done(p, b)
    return c == E and [(x["text"], x["state"], x["tries"]) for x in u] == [("文字a", "unknown", 2)] and "重試被拒" in u[0]["why"] and len(cids) == 2 and cids[0] == cids[1], (c, u, cids)


async def t34():
    async def upf(i): return {"ok": False, "msg": "x"} if i == 1 else {"ok": True, "att": "att2"}
    p, b, pg, log, ups = await mk(asker(["ok"]), upload=upf)
    await pg.evaluate(A_JS + SEND_JS); await pg.wait_for_timeout(600)
    await retry_same(pg); await pg.wait_for_timeout(700)
    c, u = await snap(pg); a = asks(log); await done(p, b)
    return c == E and u == [] and len(a) == 1 and len(ups) == 2 and ups[0]["cid"] == ups[1]["cid"] == a[0]["cid"], (c, u, len(a), [x.get("cid") for x in ups])


async def t35():
    st = {"day": "2026-10-24", "rounds": {"10:00": {"at": "2026-10-24T10:00:00", "result": "done", "drafts": [{"card_id": "c1", "chat": "甲", "draft": "稿"}]}}}
    async def h(q, log):
        if q.get("op") == "state": return dict(STATE, state=st)
        return await asker(["ABORT", "ok"])(q, log) if False else await _h35(q, log)
    seq = {"i": 0}
    async def _h35(q, log):
        if q.get("op") == "ask":
            seq["i"] += 1
            return "ABORT" if seq["i"] == 1 else await base(q, log)
        return await base(q, log)
    p, b, pg, log, ups = await mk(h)
    await pg.evaluate(js_type("1 回了") + SEND_JS); await pg.wait_for_timeout(600)
    await pg.evaluate("DATA.state={day:'2026-10-24',rounds:{'09:00':{at:'2026-10-24T09:00:00',result:'done',drafts:[{card_id:'c0',chat:'丙',draft:'稿0'}]},'10:00':{at:'2026-10-24T10:00:00',result:'done',drafts:[{card_id:'c1',chat:'甲',draft:'稿'}]}}}; render(); 0;")
    await retry_same(pg); await pg.wait_for_timeout(700)
    u = await pg.evaluate(PEND); a = asks(log); await done(p, b)
    return len(a) == 2 and a[0]["cid"] == a[1]["cid"] and a[0]["refs"] == a[1]["refs"] == json.dumps({"1": "c1"}, separators=(",", ":")) and u == [], ([(x["cid"], x["refs"]) for x in a], u)


async def t36():
    p, b, pg, log, ups = await mk(asker(["ok"]))
    out = []
    for img, q in ((None, None), (IA, None), (None, "qA"), (IA, "qA")):
        await pg.evaluate(js_compose("x", q, img) + "0;"); await _teach(pg)
        await pg.click("#tasks"); await pg.wait_for_timeout(100); await pg.click("#back"); await pg.evaluate("render(); 0;"); await pg.wait_for_timeout(100)
        c = await pg.evaluate(COMP)
        vis = await pg.evaluate("[document.getElementById('ap').getBoundingClientRect().height>0, document.getElementById('rq').getBoundingClientRect().height>0, document.getElementById('barbox').className]")
        ok = c == {"text": "x", "quote": q, "img": A64 if img else None} and vis[0] == bool(img) and vis[1] == bool(q) \
            and ("att" in vis[2].split()) == bool(img) and ("quoting" in vis[2].split()) == bool(q)
        out.append((ok, c, vis))
    n = len(asks(log)); await done(p, b)
    return all(x[0] for x in out) and n == 0, out


async def t37():
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari Line/14.17.0"
    p, b, pg, log, ups = await mk(base, ua=ua)
    await pg.mouse.wheel(0, 3000); await pg.wait_for_timeout(200); await pg.evaluate("window.scrollTo(0,0)"); await pg.wait_for_timeout(300)
    r = await pg.evaluate("""(function(){var a=document.querySelector('#inapp a[href*=openExternalBrowser]'); if(!a) return null; var b=a.getBoundingClientRect();
        var t=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2); return {hit: t===a || a.contains(t), href:a.href, w:document.documentElement.scrollWidth};})()""")
    c, u = await snap(pg); await done(p, b)
    p2, b2, pg2, _, _ = await mk(base)
    none = await pg2.evaluate("document.getElementById('inapp') === null"); await done(p2, b2)
    return bool(r and r["hit"] and r["href"].endswith("?openExternalBrowser=1") and r["w"] <= 390) and none and c == E and u == [], (r, none)


# ───────── 4agent 4A-20261024-f7b518（Codex 審 6bfa400 P1 組字被蓋／P2 晚到表情回應覆蓋）─────────
CSTART = "Q.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})); 0;"
def js_composing(v):   # 組字中：DOM 值變了、input 帶 isComposing
    return "Q.value=%s; Q.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true})); 0;" % json.dumps(v)
def js_cend(v, trailing=True):   # 選字完成：compositionend（可再補一個尾隨 input，兩種瀏覽器順序都測）
    return ("Q.value=%s; Q.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})); " % json.dumps(v)
            + ("Q.dispatchEvent(new InputEvent('input',{bubbles:true}));" if trailing else "") + " 0;")
RAW = "COMPOSER.rawText"


async def t38():   # Codex P1 原序列：既有→選圖→組字中→圖片完成→選字完成
    p, b, pg, log, ups = await mk(base, init=HOLDIMG)
    await pg.evaluate(js_type("既有"))
    await pg.set_input_files("#file", files=[{"name": "a.png", "mimeType": "image/png", "buffer": png(2, 2)}])
    await pg.wait_for_function("window.__imgq.length>=1", timeout=5000)
    await pg.evaluate(CSTART + js_composing("既有注音中"))
    await pg.evaluate("window.__imgq[0].go(); 0;"); await pg.wait_for_timeout(200)
    mid = await pg.evaluate("Q.value")
    await pg.evaluate(js_cend("既有確定")); await pg.wait_for_timeout(100)
    end = await pg.evaluate("[Q.value, %s, !!window.ATT]" % RAW); await done(p, b)
    return mid == "既有注音中" and end == ["既有確定", "既有確定", True], (mid, end)


async def t39():   # 組字中改引用、取消圖：文字不動
    p, b, pg, log, ups = await mk(base)
    await pg.evaluate(js_type("前文") + CSTART + js_composing("前文ㄓㄨ"))
    await pg.evaluate("setReply('引一句'); setAtt(%s); setAtt(null); 0;" % IA); await pg.wait_for_timeout(100)
    v = await pg.evaluate("Q.value"); q = await pg.evaluate("REPLY"); await done(p, b)
    return v == "前文ㄓㄨ" and q == "引一句", (v, q)


async def t40():   # 組字中按「教它一項任務」：拒絕改字（不奪權）
    p, b, pg, log, ups = await mk(base)
    await pg.evaluate(js_type("早上") + CSTART + js_composing("早上ㄎㄢ"))
    await pg.click("#plus"); await pg.wait_for_timeout(150); await click_text(pg, "教它一項任務"); await pg.wait_for_timeout(150)
    v = await pg.evaluate("[Q.value, %s]" % RAW); await done(p, b)
    return v == ["早上ㄎㄢ", "早上"], v


async def t41():   # 組字中失焦、compositionend 沒發：不能卡死，之後能送出
    p, b, pg, log, ups = await mk(base)
    await pg.evaluate(CSTART + js_composing("看一下"))
    await pg.evaluate("Q.dispatchEvent(new FocusEvent('blur')); 0;"); await pg.wait_for_timeout(100)
    st = await pg.evaluate("[COMPOSING, %s]" % RAW)
    await pg.evaluate(SEND_JS); await pg.wait_for_timeout(600)
    n = [x.get("text") for x in asks(log)]; await done(p, b)
    return st == [False, "看一下"] and n == ["看一下"], (st, n)


async def t42():   # 選字完成只發 compositionend、沒有尾隨 input（Chrome 序）：仍採用最終文字
    p, b, pg, log, ups = await mk(base)
    await pg.evaluate(CSTART + js_composing("ㄋㄧ") + js_cend("你", trailing=False)); await pg.wait_for_timeout(50)
    v = await pg.evaluate("[Q.value, %s]" % RAW); await done(p, b)
    return v == ["你", "你"], v


async def t43():   # 組字中送出：不送、不清空
    p, b, pg, log, ups = await mk(base)
    await pg.evaluate(CSTART + js_composing("ㄏㄠ")); await pg.evaluate(SEND_JS); await pg.wait_for_timeout(400)
    v = await pg.evaluate("Q.value"); n = len(asks(log)); await done(p, b)
    return v == "ㄏㄠ" and n == 0, (v, n)


BOT = {"id": "b1", "role": "bot", "text": "好的", "at": "2026-10-24T10:00:00"}


def react_cloud(gate, reply_chat):
    """react 的回應被 gate 擋住；放行時回舊快照 reply_chat。其餘照雲端狀態（ask 收下後 chat 會含那則）。"""
    cloud = {"chat": [dict(BOT)]}
    async def h(q, log):
        op = q.get("op")
        if op == "react":   # 伺服器收到就存（之後的輪詢看得到）；回應被擋住，放行時帶的是收件當下的舊快照
            cloud["chat"] = [dict(cloud["chat"][0], reaction=q.get("emoji") or None)] + cloud["chat"][1:]
            await gate.wait(); return {"ok": True, "chat": reply_chat}
        if op == "ask":
            m = {"id": "m" + q["cid"], "role": "me", "text": q["text"], "status": "pending", "at": "2026-10-24T10:01:00"}
            cloud["chat"] = cloud["chat"] + [m]; return {"ok": True, "chat": cloud["chat"]}
        return await base(q, log, chat=cloud["chat"])
    return h


async def t44():   # Codex P2 原序列：表情未回→送新訊息→輪詢確認→舊表情回應抵達：新訊息不得消失
    gate = asyncio.Event()
    p, b, pg, log, ups = await mk(react_cloud(gate, [dict(BOT, reaction="👍")]))   # 正常輪詢間隔：檢查點落在下一輪輪詢之前
    await pg.evaluate("void react('b1','👍'); 0;"); await pg.wait_for_timeout(100)
    await pg.evaluate(js_type("新的一題") + SEND_JS); await pg.wait_for_timeout(4600)   # 等一輪 4 秒輪詢確認收件（seenInCloud）
    gate.set(); await pg.wait_for_timeout(300)
    ids = await pg.evaluate("chat().map(function(x){return x.id+':'+(x.reaction||'')})"); await done(p, b)
    return len(ids) == 2 and ids[0] == "b1:👍" and ids[1].startswith("m"), ids


async def t45():   # 表情樂觀顯示：被輪詢換掉前後都在；雲端快照帶回後清掉覆蓋層
    gate = asyncio.Event(); gate.set()
    cloud_rx = {"on": False}
    async def h(q, log):
        if q.get("op") == "react":
            cloud_rx["on"] = True; return {"ok": True, "chat": [dict(BOT, reaction="❤️")]}
        return await base(q, log, chat=[dict(BOT, reaction="❤️")] if cloud_rx["on"] else [dict(BOT)])
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.evaluate("void react('b1','❤️'); 0;"); await pg.wait_for_timeout(300)
    a = await pg.evaluate("chat()[0].reaction||''")
    await pg.evaluate("poll(); 0;"); await pg.wait_for_timeout(400)
    c = await pg.evaluate("[chat()[0].reaction||'', Object.keys(window.RXPEND||{}).length]"); await done(p, b)
    return a == "❤️" and c == ["❤️", 0], (a, c)


async def t46():   # 表情失敗：只撤銷那一次，顯示回原樣
    async def h(q, log):
        if q.get("op") == "react": return {"ok": False, "msg": "x"}
        return await base(q, log, chat=[dict(BOT)])
    p, b, pg, log, ups = await mk(h)
    await pg.evaluate("void react('b1','👍'); 0;"); await pg.wait_for_timeout(400)
    v = await pg.evaluate("chat()[0].reaction||''"); await done(p, b)
    return v == "", v


async def t47():   # Claude Code 交辦單的通知（task_id tccwork，claude_bot_work.py）不標成「例行任務」；一般例行任務照舊
    W = {"id": "brwork12345s", "role": "bot", "kind": "task", "task_id": "tccwork", "task_name": "Claude Code 交辦單",
         "text": "開始處理你的交辦", "at": "2026-10-24T10:00:00"}
    T = {"id": "brtask12345", "role": "bot", "kind": "task", "task_id": "tabcdef12", "task_name": "早報",
         "text": "今天沒有新聞", "at": "2026-10-24T10:01:00"}
    async def h(q, log):
        return await base(q, log, chat=[W, T])
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(400)
    txt = await pg.evaluate("document.body.innerText"); await done(p, b)
    ok = "Claude Code・交辦單" in txt and "例行任務・Claude Code" not in txt and "例行任務・早報" in txt
    return ok, [ln for ln in txt.splitlines() if "交辦" in ln or "例行任務" in ln][:4]


async def t48():   # 交辦單「✓ 做完了：…」整則就是報告，留在面板內正文大小（律師 10-24：最下面的說明不起眼）；一般回答末尾的 ✓ 動作行照舊置中
    W = {"id": "brwork12345d", "role": "bot", "kind": "task", "task_id": "tccwork", "task_name": "Claude Code 交辦單",
         "text": "✓ 做完了：已改好，你不用另外做什麼。", "at": "2026-10-24T10:00:00"}
    A = {"id": "bans12345", "role": "bot", "text": "我把你的原話交給 Claude Code。\n✓ 已開交辦單", "at": "2026-10-24T10:01:00"}
    async def h(q, log):
        return await base(q, log, chat=[W, A])
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(400)
    v = await pg.evaluate("""() => { const f = s => [...document.querySelectorAll(s)].map(e => e.textContent);
      return {txt: f('.msg .txt'), sys: f('.sys')}; }""")
    await done(p, b)
    ok = (any("做完了：已改好" in t for t in v["txt"]) and not any("做完了" in s for s in v["sys"])
          and "已開交辦單" in v["sys"] and not any("已開交辦單" in t for t in v["txt"]))
    return ok, v


# ── 律師 10-24 22:14「少了許多功能，比如選取文字」／22:19「希望能有進度條，不然不知道是故障還是施工中」──
import datetime as _dt
def _iso_ago(ms_ago, base_ms):
    return _dt.datetime.fromtimestamp((base_ms - ms_ago) / 1000, _dt.timezone(_dt.timedelta(hours=8))).isoformat(timespec="seconds")


async def t49():   # 長按／右鍵選單有「選取文字」：開全螢幕面板、整則可選取、完成就關
    LONG = dict(BOT, text="第一行：甲乙丙\n第二行：丁戊己")
    async def h(q, log):
        return await base(q, log, chat=[LONG])
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(400)
    await pg.dispatch_event(".msg", "contextmenu")
    await pg.locator("#mcard button", has_text="選取文字").click()
    v = await pg.evaluate("""() => ({on: document.getElementById('selv').className, t: document.getElementById('selt').textContent,
      us: getComputedStyle(document.getElementById('selt')).userSelect})""")
    await pg.click("#selX")
    off = await pg.evaluate("document.getElementById('selv').className")
    await done(p, b)
    return v["on"] == "on" and v["t"] == LONG["text"] and v["us"] == "text" and off == "", (v, off)


async def t50():   # 電腦（有滑鼠）上泡泡文字可直接拖曳選取；觸控裝置才關掉（長按要叫選單）
    async def h(q, log):
        return await base(q, log, chat=[dict(BOT)])
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(400)
    v = await pg.evaluate("({hover: matchMedia('(hover:hover)').matches, us: getComputedStyle(document.querySelector('.msg')).userSelect})")
    await done(p, b)
    return v["hover"] and v["us"] != "none", v


def _work(now_ms, rx_ago=10_000, run_last_ago=30_000):
    return {"at": _iso_ago(rx_ago, now_ms), "rx": now_ms - rx_ago, "now": now_ms, "items": [
        {"id": "w1", "text": "希望能有進度條", "status": "running", "tries": 1, "max_tries": 2, "timeout_min": 45,
         "started_at": _iso_ago(600_000, now_ms), "steps": 12, "last_act": _iso_ago(run_last_ago, now_ms), "doing": "跑指令（測試／檢查）"},
        {"id": "w2", "text": "補齊選取文字", "status": "queued", "tries": 0, "max_tries": 2, "timeout_min": 45, "ahead": 1},
        {"id": "w3", "text": "斷網那張", "status": "queued", "tries": 1, "max_tries": 2, "timeout_min": 45, "ahead": 2,
         "retry_at": "2026-10-25T00:30:00+08:00", "env_note": "額度用完或連不上網"}]}


async def t51():   # 進度卡：施工中（步數、最後動作、在做什麼）、排隊第幾、斷網何時再試；Mac 有在回報就不警告
    import time
    now_ms = int(time.time() * 1000)
    async def h(q, log):
        r = await base(q, log, chat=[dict(BOT)])
        r["work"] = _work(now_ms)
        return r
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(500)
    txt = await pg.evaluate("[...document.querySelectorAll('.wk')].map(e => e.innerText).join('\\n---\\n')")
    w = await pg.evaluate("[...document.querySelectorAll('.wk .pb i')].map(e => e.style.width)")
    await done(p, b)
    ok = ("施工中（第 1／2 次）" in txt and "已做 12 步" in txt and "跑指令" in txt and "前面還有 1 件" in txt
          and "00:30 自動再試" in txt and "沒有回報" not in txt and "可能卡住" not in txt and txt.count("交辦單進度") == 3
          and w[0] not in ("", "0%") and w[1] == "0%")
    return ok, (txt, w)


async def t52():   # Mac 5 分鐘沒回報 ⇒ 卡片明講「Mac 沒有回報」；施工中 10 分鐘沒動作 ⇒「可能卡住」
    import time
    now_ms = int(time.time() * 1000)
    async def h(q, log):
        r = await base(q, log, chat=[dict(BOT)])
        r["work"] = _work(now_ms, rx_ago=300_000, run_last_ago=600_000)
        return r
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(500)
    txt = await pg.evaluate("[...document.querySelectorAll('.wk')].map(e => e.innerText).join('\\n')")
    await done(p, b)
    return "沒有回報進度" in txt and "可能卡住" in txt, txt


async def t53():   # 輪詢帶回新進度就重畫：施工中 → 做完了（有進行中的單時不是 30 秒才看一次）
    import time
    now_ms = int(time.time() * 1000)
    n = {"state": 0}
    fin = {"at": "x", "rx": now_ms, "now": now_ms, "items": [{"id": "w1", "text": "希望能有進度條", "status": "done",
           "tries": 1, "max_tries": 2, "timeout_min": 45, "finished_at": _iso_ago(0, now_ms)}]}
    async def h(q, log):
        r = await base(q, log, chat=[dict(BOT)])
        if q.get("op") == "state":
            n["state"] += 1
            r["work"] = _work(now_ms) if n["state"] == 1 else fin
        return r
    fast10 = "(function(){var st=window.setTimeout;window.setTimeout=function(f,d){if(d===10000)d=80;return st.call(window,f,d);};})();"
    p, b, pg, log, ups = await mk(h, init=fast10)
    await pg.wait_for_timeout(1500)
    txt = await pg.evaluate("[...document.querySelectorAll('.wk')].map(e => e.innerText).join('\\n')")
    await done(p, b)
    return "做完了" in txt and "施工中" not in txt and n["state"] >= 2, (txt, n)



async def t54():   # 舊快照晚到不倒退；同一份快照較晚重收時「Mac 多久沒回報」照算（不歸零）；null 不清掉
    import time
    now_ms = int(time.time() * 1000)
    fin = {"at": "x", "rx": now_ms, "now": now_ms, "items": [{"id": "w1", "text": "希望能有進度條", "status": "done",
           "tries": 1, "max_tries": 2, "timeout_min": 45, "finished_at": _iso_ago(0, now_ms)}]}
    async def h(q, log):
        return await base(q, log, chat=[dict(BOT)])
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(300)
    await pg.evaluate("w => { takeWork(w[0]); takeWork(w[1]); takeWork(null); render(); }", [fin, _work(now_ms - 60_000)])
    t1 = await pg.evaluate("document.getElementById('wkbox').innerText")
    run = _work(now_ms)                                   # rx＝now-10 秒
    later = dict(run, rx=run["rx"], now=now_ms + 300_000)  # 同一份快照、5 分鐘後再讀到
    await pg.evaluate("w => { WORK = null; WORK_NOW = 0; takeWork(w[0]); takeWork(w[1]); render(); }", [run, later])
    t2 = await pg.evaluate("document.getElementById('wkbox').innerText")
    await done(p, b)
    return ("做完了" in t1 and "施工中" not in t1 and "沒有回報進度" in t2), (t1, t2)


async def t55():   # 手機真的讀取失敗 90 秒以上：寫明是手機斷線（不是 Mac）
    import time
    now_ms = int(time.time() * 1000)
    init = ("window.__off=0;(function(){var n=Date.now;Date.now=function(){return n()+window.__off;};"
            "var st=window.setTimeout;window.setTimeout=function(f,d){if(d===10000)d=80;return st.call(window,f,d);};})();")
    dead = {"on": False}
    async def h(q, log):
        if dead["on"]:
            return "ABORT"
        r = await base(q, log, chat=[dict(BOT)])
        if q.get("op") == "state":
            r["work"] = _work(now_ms)
        return r
    p, b, pg, log, ups = await mk(h, init=init)
    await pg.wait_for_timeout(500)
    dead["on"] = True
    await pg.wait_for_timeout(800)
    await pg.evaluate("window.__off = 4*60*1000; repaintWork();")
    txt = await pg.evaluate("document.getElementById('wkbox').innerText")
    await done(p, b)
    return "讀不到雲端" in txt and "Mac 已經" not in txt, txt


async def t58():   # 打字中輪詢暫停 4 分鐘：不得誤報手機斷線或 Mac 沒回報；進度區照重畫並註明幾點讀到的
    import time
    now_ms = int(time.time() * 1000)
    init = "window.__off=0;(function(){var n=Date.now;Date.now=function(){return n()+window.__off;};})();"
    async def h(q, log):
        r = await base(q, log, chat=[dict(BOT)])
        if q.get("op") == "state":
            r["work"] = _work(now_ms)
        return r
    p, b, pg, log, ups = await mk(h, init=init)
    await pg.wait_for_timeout(500)
    await pg.evaluate("""() => { const t = document.createElement('textarea'); t.id = 'probeEdit'; document.body.appendChild(t); t.focus();
      window.__off = 4*60*1000; repaintWork(); }""")
    v = await pg.evaluate("({ed: editing(), txt: document.getElementById('wkbox').innerText})")
    await done(p, b)
    t = v["txt"]
    return v["ed"] and "讀到的進度" in t and "讀不到雲端" not in t and "沒有回報" not in t and "可能卡住" not in t, v


async def t56():   # 跨兩則選字後在第二則按右鍵：交給瀏覽器原生選單（不 preventDefault）
    A, B = dict(BOT, id="b1", text="第一則文字"), dict(BOT, id="b2", text="第二則文字", at="2026-10-24T10:01:00")
    async def h(q, log):
        return await base(q, log, chat=[A, B])
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(400)
    v = await pg.evaluate("""() => { const t = [...document.querySelectorAll('.msg .txt')];
      const r = document.createRange(); r.setStart(t[0].firstChild, 1); r.setEnd(t[1].firstChild, 2);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
      const e = new MouseEvent('contextmenu', {bubbles: true, cancelable: true}); t[1].dispatchEvent(e);
      return {prevented: e.defaultPrevented, menu: document.getElementById('menu').className}; }""")
    await done(p, b)
    return v["prevented"] is False and v["menu"] == "", v



async def t57():   # 本機排隊太多時送來的降級列「另外還有 N 件」要顯示成一行，不是空白卡
    import time
    now_ms = int(time.time() * 1000)
    w = _work(now_ms); w["items"].append({"id": "more", "status": "more", "count": 12})
    async def h(q, log):
        r = await base(q, log, chat=[dict(BOT)]); r["work"] = w; return r
    p, b, pg, log, ups = await mk(h, fast=True)
    await pg.wait_for_timeout(400)
    txt = await pg.evaluate("document.getElementById('wkbox').innerText")
    await done(p, b)
    return "另外還有 12 件交辦單在排隊" in txt and "undefined" not in txt, txt


MATRIX = [t01, t02, t03, t04, t05, t06, t07, t08, t09, t10, t11, t12, t13, t14, t15, t16, t17, t18, t19, t20,
          t21, t22, t23, t24, t25, t26, t27, t28, t29, t30, t31, t32, t33, t34, t35, t36, t37,
          t38, t39, t40, t41, t42, t43, t44, t45, t46, t47, t48, t49, t50, t51, t52, t53, t54, t55, t56, t57, t58]
EXISTING = [t_cid_reused_after_timeout, t_failure_keeps_new_draft_and_quote, t_stale_poll_does_not_drop_new_message,
            t_teach_menu_keeps_draft, t_teach_record_flow, t_teach_review_84b793d, t_teach_out_of_order, t_teach_stop_response_late, t_layout_class_survives_quote_cancel, t_upload_does_not_eat_next_image_or_quote,
            t_fail_after_typing_keeps_draft_and_unsent_full, t_fail_untouched_keeps_whole_message,
            t_type_then_clear_counts_as_touched, t_choices_show_despite_unsent, t_line_inapp_banner_390,
            t_draft_edit_store_intact, t_teach_during_pending_not_overwritten, t_line_banner_visible]


async def main():
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
    tally = {}
    for group, fs in (("existing", EXISTING), ("matrix", MATRIX)):
        for f in fs:
            if only and f.__name__ not in only and group not in only:
                continue
            try:
                ok, got = await asyncio.wait_for(f(), 60)
            except Exception as e:  # noqa: BLE001
                ok, got = False, f"{type(e).__name__}: {e}"
            tally.setdefault(group, [0, 0])[0 if ok else 1] += 1
            print(("PASS" if ok else "FAIL"), group, f.__name__, str(got)[:400], flush=True)
    for g, (a, z) in tally.items():
        print(f"SUMMARY {g}: PASS {a} / FAIL {z} / total {a + z}", flush=True)
    ran = sum(a + z for a, z in tally.values())
    return 1 if ran == 0 or any(z for _, z in tally.values()) else 0   # 有任何 FAIL（或一項都沒跑到）就非零退出（Codex 審 d2a31e3：FAIL 9 仍 exit 0）

sys.exit(asyncio.run(main()))
