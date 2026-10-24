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


async def page_with(handler, ua=None, fast=False, init=None, liff=None):
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
            return await r.fulfill(body=liff or LIFF, content_type="application/javascript")
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


def _work(now_ms, rx_ago=10_000, idle_s=30, seq=None):
    return {"at": "x", "rx": now_ms - rx_ago, "now": now_ms, "seq": seq or (now_ms - rx_ago), "items": [
        {"id": "w1", "text": "希望能有進度條", "status": "running", "tries": 1, "max_tries": 2, "timeout_min": 45,
         "elapsed_s": 600, "steps": 12, "idle_s": idle_s, "doing": "跑指令（測試／檢查）"},
        {"id": "w2", "text": "補齊選取文字", "status": "queued", "tries": 0, "max_tries": 2, "timeout_min": 45, "ahead": 1},
        {"id": "w3", "text": "斷網那張", "status": "queued", "tries": 1, "max_tries": 2, "timeout_min": 45, "ahead": 2,
         "retry_in_s": 900, "env_note": "額度用完或連不上網"}]}


def _done_work(now_ms, posted=True, seq=None):
    return {"at": "x", "rx": now_ms, "now": now_ms, "seq": seq or now_ms, "items": [{"id": "w1", "text": "希望能有進度條", "status": "done",
            "tries": 1, "max_tries": 2, "timeout_min": 45, "ago_s": 0, "notice_posted": posted}]}


WK_TXT = "(document.getElementById('wkbox')||{innerText:''}).innerText"


async def _wk_page(work_for_state=None, init=None, fast=True):
    async def h(q, log):
        r = await base(q, log, chat=[dict(BOT)])
        if work_for_state is not None and q.get("op") in ("state", "chat"):
            r["work"] = work_for_state(q)
        return r
    p, b, pg, log, ups = await mk(h, fast=fast, init=init)
    await pg.wait_for_timeout(500)
    return p, b, pg


async def t51():   # 進度卡：施工中（第幾次、時間已用、動作數、最後動作、在做什麼）、排隊第幾、斷網約多久再試；雲端有收到就不警告
    import time
    now_ms = int(time.time() * 1000)
    p, b, pg = await _wk_page(lambda q: _work(now_ms))
    txt = await pg.evaluate("[...document.querySelectorAll('.wk')].map(e => e.innerText).join('\\n---\\n')")
    w = await pg.evaluate("[...document.querySelectorAll('.wk .pb i')].map(e => e.style.width)")
    await done(p, b)
    ok = ("施工中（第 1／2 次）・時間已用 10 分鐘" in txt and "已做 12 個動作・最後一個是 40 秒前" in txt and "跑指令" in txt
          and "前面還有 1 件" in txt and "約 15 分鐘後自動再試" in txt and "沒收到" not in txt and "可能卡住" not in txt
          and txt.count("交辦單進度") == 3 and w[0] not in ("", "0%") and w[1] == "0%")
    return ok, (txt, w)


async def t52():   # 雲端 5 分鐘沒收到 Mac 回報 ⇒ 明講（且不另判卡住）；雲端有收到但 10 分鐘沒動作 ⇒「可能卡住」；綁不到 ⇒ 照實說接不上
    import time
    now_ms = int(time.time() * 1000)
    p, b, pg = await _wk_page()
    unb = _work(now_ms, seq=now_ms + 2); unb["items"][0].pop("idle_s"); unb["items"][0]["activity"] = "unbound"
    t = await pg.evaluate("""a => { const out = [];
      workObserve({ok: true, work: a[0]}); out.push(%s);
      WORK = null; WORK_NOW = 0; workObserve({ok: true, work: a[1]}); out.push(%s);
      WORK = null; WORK_NOW = 0; workObserve({ok: true, work: a[2]}); out.push(%s); return out; }""" % (WK_TXT, WK_TXT, WK_TXT),
        [_work(now_ms, rx_ago=300_000, idle_s=600), _work(now_ms, idle_s=600, seq=now_ms + 1), unb])
    await done(p, b)
    return ("沒收到 Mac 的回報" in t[0] and "可能卡住" not in t[0] and "可能卡住" in t[1] and "沒收到" not in t[1]
            and "動作紀錄暫時接不上" in t[2] and "已做" not in t[2]), t


async def t53():   # 輪詢帶回新版本就重畫：施工中 → 做完了（有進行中的單時 10 秒一次）
    import time
    now_ms = int(time.time() * 1000)
    n = {"c": 0}

    def wf(q):
        n["c"] += 1
        return _work(now_ms) if n["c"] == 1 else _done_work(now_ms, seq=now_ms + 5)
    fast10 = "(function(){var st=window.setTimeout;window.setTimeout=function(f,d){if(d===10000)d=80;return st.call(window,f,d);};})();"
    p, b, pg = await _wk_page(wf, init=fast10, fast=False)
    await pg.wait_for_timeout(1500)
    txt = await pg.evaluate(WK_TXT)
    await done(p, b)
    return "做完了" in txt and "施工中" not in txt and n["c"] >= 2, (txt, n)


async def t54():   # 舊版本晚到不倒退；同一份較晚重讀時「雲端多久沒收到」照算；work:null 不清掉卡片、算讀取異常
    import time
    now_ms = int(time.time() * 1000)
    p, b, pg = await _wk_page()
    run = _work(now_ms)
    t = await pg.evaluate("""a => { const out = [];
      workObserve({ok: true, work: a[0]}); workObserve({ok: true, work: a[1]}); out.push(%s);
      WORK = null; WORK_NOW = 0; workObserve({ok: true, work: a[2]}); workObserve({ok: true, work: a[3]}); out.push(%s);
      workObserve({ok: true, work: null}); out.push(String(WORK && WORK.items.length) + '|' + String(!!WORK_FAIL_AT)); return out; }""" % (WK_TXT, WK_TXT),
        [_done_work(now_ms, seq=now_ms + 9), _work(now_ms, seq=now_ms + 1), run, dict(run, now=now_ms + 300_000)])
    await done(p, b)
    return "做完了" in t[0] and "施工中" not in t[0] and "沒收到 Mac 的回報" in t[1] and t[2] == "3|true", t


async def t55():   # 手機真的讀取失敗 90 秒以上：寫明是手機連不上（不是 Mac）
    import time
    now_ms = int(time.time() * 1000)
    init = ("window.__off=0;(function(){var n=Date.now;Date.now=function(){return n()+window.__off;};var pn=performance.now.bind(performance);performance.now=function(){return pn()+window.__off;};"
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
    await pg.evaluate("window.__off = 4*60*1000; paintWork();")
    txt = await pg.evaluate(WK_TXT)
    await done(p, b)
    return "讀不到雲端" in txt and "沒收到 Mac" not in txt, txt


async def t57():   # 本機排隊太多時送來的降級列「另外還有 N 件」要顯示成一行
    import time
    now_ms = int(time.time() * 1000)
    w = _work(now_ms); w["items"].append({"id": "more", "status": "more", "count": 12})
    p, b, pg = await _wk_page(lambda q: w)
    txt = await pg.evaluate(WK_TXT)
    await done(p, b)
    return "另外還有 12 件交辦單在排隊" in txt and "undefined" not in txt, txt


async def t58():   # 打字中輪詢暫停 4 分鐘：不得誤報手機斷線、Mac 沒回報或卡住；進度區照重畫並註明幾點讀到的
    import time
    now_ms = int(time.time() * 1000)
    init = "window.__off=0;(function(){var n=Date.now;Date.now=function(){return n()+window.__off;};var pn=performance.now.bind(performance);performance.now=function(){return pn()+window.__off;};})();"
    p, b, pg = await _wk_page(lambda q: _work(now_ms), init=init, fast=False)
    await pg.evaluate("""() => { const t = document.createElement('textarea'); t.id = 'probeEdit'; document.body.appendChild(t); t.focus();
      window.__off = 4*60*1000; paintWork(); }""")
    v = await pg.evaluate("({ed: editing(), txt: %s})" % WK_TXT)
    await done(p, b)
    t = v["txt"]
    return v["ed"] and "讀到的進度" in t and "讀不到雲端" not in t and "沒收到" not in t and "可能卡住" not in t, v


async def t59():   # 讀取結果分類（設計者 T09）：ok:false、缺 work、壞格式、例外都算讀取異常且保留舊卡；有效空清單才清空並恢復
    import time
    now_ms = int(time.time() * 1000)
    init = "window.__off=0;(function(){var n=Date.now;Date.now=function(){return n()+window.__off;};var pn=performance.now.bind(performance);performance.now=function(){return pn()+window.__off;};})();"
    p, b, pg = await _wk_page(lambda q: _work(now_ms), init=init, fast=False)
    v = await pg.evaluate("""a => { const out = [];
      for (const r of [{ok: false, msg: 'forbidden'}, {ok: true}, {ok: true, work: {items: 'x'}}, null]) {
        WORK_FAIL_AT = 0; workObserve(r); out.push(!!WORK_FAIL_AT && WORK.items.length === 3); }
      window.__off = 4*60*1000; paintWork(); out.push(%s.includes('讀不到雲端'));
      workObserve({ok: true, work: a}); out.push(WORK.items.length === 0 && !WORK_FAIL_AT && !%s.includes('交辦單進度'));
      return out; }""" % (WK_TXT, WK_TXT), {"at": "x", "rx": now_ms + 1, "now": now_ms + 2, "seq": now_ms + 99, "items": []})
    await done(p, b)
    return all(v), v


async def t60():   # 打字中首次收到進度（設計者 T10）：馬上出卡片；輸入框焦點與內容不變
    import time
    now_ms = int(time.time() * 1000)
    p, b, pg = await _wk_page()
    v = await pg.evaluate("""w => { const t = document.createElement('textarea'); t.id = 'probeEdit'; t.value = '打到一半'; document.body.appendChild(t); t.focus();
      const before = !!document.getElementById('wkbox') && document.getElementById('wkbox').innerText;
      workObserve({ok: true, work: w});
      t.setSelectionRange(1, 3);
      workObserve({ok: true, work: Object.assign({}, w, {seq: w.seq + 1})});
      return {before: before, after: %s, focus: document.activeElement === t, val: t.value, sel: [t.selectionStart, t.selectionEnd]}; }""" % WK_TXT, _work(now_ms))
    await done(p, b)
    return (not v["before"]) and "交辦單進度" in v["after"] and v["focus"] and v["val"] == "打到一半" and v["sel"] == [1, 3], v


async def t61():   # 完成與通知分離（設計者 T14）：通知還沒送到就不能說「結果貼在上面」
    import time
    now_ms = int(time.time() * 1000)
    p, b, pg = await _wk_page()
    t = await pg.evaluate("""a => { workObserve({ok: true, work: a[0]}); const x = %s;
      workObserve({ok: true, work: a[1]}); return [x, %s]; }""" % (WK_TXT, WK_TXT),
        [_done_work(now_ms, posted=False, seq=now_ms + 1), _done_work(now_ms, posted=True, seq=now_ms + 2)])
    await done(p, b)
    return "結果通知還在送" in t[0] and "已送進對話" not in t[0] and "結果已送進對話" in t[1], t


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



async def t62():   # Codex 審 1a4ee7f：hm() 被進度區同名函式蓋掉 ⇒ 草稿卡「已回（）」；壞項目／字串版本號不得蓋掉正常卡或讓版本倒退
    import time
    now_ms = int(time.time() * 1000)
    p, b, pg = await _wk_page()
    v = await pg.evaluate("""a => { const out = {hm: hm('2026-10-24T10:05:00')};
      workObserve({ok: true, work: a[0]}); const good = WORK.seq;
      for (const items of [[null], [{}], [{id: 'x', text: 't', status: 'weird'}]]) {
        WORK_FAIL_AT = 0; workObserve({ok: true, work: Object.assign({}, a[0], {seq: good + 5, items: items})});
        if (WORK.seq !== good || !WORK_FAIL_AT) out.bad = (out.bad || 0) + 1; }
      workObserve({ok: true, work: Object.assign({}, a[0], {seq: String(good + 9)})});
      out.strSeqRejected = WORK.seq === good;
      out.txt = %s; return out; }""" % WK_TXT, [_work(now_ms)])
    await done(p, b)
    return v["hm"] == "10:05" and not v.get("bad") and v["strSeqRejected"] and "undefined" not in v["txt"] and "施工中" in v["txt"], v



async def t63():   # 設計者 ec1352 T02／T08／T09：欄位型別錯整份拒收；通知狀態缺欄＝不明；進度條是活動條（新動作流動、久無動作變灰），不是時間比例
    import time
    now_ms = int(time.time() * 1000)
    p, b, pg = await _wk_page()
    base_w = _work(now_ms)
    bad = []
    for mut in ({"elapsed_s": "600"}, {"steps": -1}, {"idle_s": -5}, {"ahead": 1.5}):
        w = json.loads(json.dumps(base_w)); w["seq"] += 1; w["items"][0].update(mut); bad.append(w)
    w2 = json.loads(json.dumps(base_w)); w2["seq"] += 1; w2["items"].append({"id": "more", "status": "more", "count": -1}); bad.append(w2)
    w3 = json.loads(json.dumps(_done_work(now_ms))); w3["seq"] = base_w["seq"] + 1; w3["items"][0]["notice_posted"] = "false"; bad.append(w3)
    w4 = json.loads(json.dumps(base_w)); w4["seq"] = 2 ** 60; bad.append(w4)
    unk = json.loads(json.dumps(_done_work(now_ms))); unk["seq"] = base_w["seq"] + 4; unk["items"][0].pop("notice_posted")   # 版本比 quiet 新
    quiet = json.loads(json.dumps(base_w)); quiet["seq"] = base_w["seq"] + 3; quiet["items"][0]["idle_s"] = 600
    v = await pg.evaluate("""a => { const out = {rej: []};
      workObserve({ok: true, work: a.base}); const good = WORK.seq;
      out.lively = document.querySelector('.wk .pb i').className;
      for (const w of a.bad) { WORK_FAIL_AT = 0; workObserve({ok: true, work: w}); out.rej.push(WORK.seq === good && !!WORK_FAIL_AT); }
      workObserve({ok: true, work: a.quiet}); out.quiet = document.querySelector('.wk .pb i').className;
      out.width = document.querySelector('.wk .pb i').style.width;
      workObserve({ok: true, work: a.unk}); out.unk = %s; return out; }""" % WK_TXT, {"base": base_w, "bad": bad, "unk": unk, "quiet": quiet})
    await done(p, b)
    return (all(v["rej"]) and len(v["rej"]) == 7 and v["lively"] == "run" and v["quiet"] == "idle" and v["width"] == "100%"
            and "結果通知狀態不明" in v["unk"]), v



async def t64():   # 設計者 ec1352 第 1 輪試修審查：①失敗後收到舊版本不得清除失敗 ②打字中真實輪詢也會建卡 ③缺欄顯示「不明」不補假數值
    import time
    now_ms = int(time.time() * 1000)
    fast = "(function(){var st=window.setTimeout;window.setTimeout=function(f,d){if(d===10000||d===30000)d=80;return st.call(window,f,d);};})();"
    first = {"n": 0}

    def wf(q):
        first["n"] += 1
        return None if first["n"] == 1 else _work(now_ms)   # 頁面剛載入時還沒有任何進度
    p, b, pg = await _wk_page(wf, init=fast, fast=False)
    await pg.evaluate("() => { const t = document.createElement('textarea'); t.id = 'probeEdit'; t.value = '打字中'; document.body.appendChild(t); t.focus(); }")
    await pg.wait_for_timeout(1200)
    v = await pg.evaluate("({card: %s, ed: editing(), val: document.getElementById('probeEdit').value})" % WK_TXT)
    old = _work(now_ms, seq=1); partial = _work(now_ms, seq=now_ms + 50)
    for k in ("tries", "elapsed_s", "steps", "timeout_min"):
        partial["items"][0].pop(k)
    v2 = await pg.evaluate("""a => { workObserve(null); const failed = !!WORK_FAIL_AT;
      workObserve({ok: true, work: a[0]}); const stillFailed = !!WORK_FAIL_AT;
      workObserve({ok: true, work: a[1]}); return {failed, stillFailed, txt: %s}; }""" % WK_TXT, [old, partial])
    await done(p, b)
    return ("交辦單進度" in v["card"] and v["ed"] and v["val"] == "打字中" and v2["failed"] and v2["stillFailed"]
            and "undefined" not in v2["txt"] and "時間已用 不明" in v2["txt"] and "動作數不明" in v2["txt"]), (v, v2)


# ── 同步骨架（#4487：電腦版、手機版都不會即時更新，要關掉重開）＋#4492 剩餘項。每一條都要「舊頁紅、新頁綠」 ──
def liff_js(profile_ok=False):
    """可換 token、會記錄 login／logout 的 LIFF 模擬；profile_ok＝LINE 自己認不認這個 token（getProfile）。"""
    return ("window.__login=[];window.__logout=0;window.__tok=['tok'];"
            "window.liff={init:()=>Promise.resolve(),isLoggedIn:()=>true,getAccessToken:()=>window.__tok[0],"
            "login:(o)=>{window.__login.push(o||{});},logout:()=>{window.__logout++;},isInClient:()=>false,"
            "getProfile:()=>%s};" % ("Promise.resolve({userId:'U1'})" if profile_ok else "Promise.reject(new Error('401'))"))


HIDDEN = ("(function(){var h=false;Object.defineProperty(document,'hidden',{configurable:true,get:function(){return h;}});"
          "Object.defineProperty(document,'visibilityState',{configurable:true,get:function(){return h?'hidden':'visible';}});"
          "window.__setHidden=function(v){h=v;document.dispatchEvent(new Event('visibilitychange'));};})();")
NEWBOT = {"id": "b2", "role": "bot", "text": "雲端新來的一則", "at": "2026-10-24T10:05:00"}
HD = "document.getElementById('hd').innerText"


def _cloud(chat=None):
    """可變的雲端：mode＝ok／forbidden／ABORT；tok_ok＝只有這些 token 雲端認。"""
    c = {"chat": chat if chat is not None else [dict(BOT)], "mode": "ok", "tok_ok": None, "n": 0}

    async def h(q, log):
        op = q.get("op")
        if op in ("state", "chat", "ask"):
            c["n"] += 1
            if c["tok_ok"] is not None and q.get("token") not in c["tok_ok"]:
                return {"ok": False, "msg": "forbidden"}
            if c["mode"] == "forbidden":
                return {"ok": False, "msg": "forbidden"}
            if c["mode"] == "ABORT":
                return "ABORT"
        return await base(q, log, chat=c["chat"])
    return c, h


async def t65():   # 雲端回 forbidden、LINE 也不認這個 token（12 小時到期／關掉 LIFF 被撤銷）⇒ 自動重新登入一次；10 分鐘內不重複跳轉
    c, h = _cloud()
    p, b, pg, log = await page_with(h, fast=True, liff=liff_js(profile_ok=False))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["mode"] = "forbidden"; await pg.wait_for_timeout(1500)
    v = await pg.evaluate("({login: window.__login, logout: window.__logout, hd: %s})" % HD)
    forb = sum(1 for x in log if x.get("op") in ("state", "chat")) - 1
    await done(p, b)
    return (len(v["login"]) == 1 and v["login"][0].get("redirectUri") == "https://x.test/" and v["logout"] >= 1
            and "登入過期" in v["hd"] and forb >= 3), (v, forb)


async def t66():   # SDK 手上已經有新 token ⇒ 直接換上、立刻重抓，不必跳轉登入
    c, h = _cloud()
    p, b, pg, log = await page_with(h, fast=True, liff=liff_js(profile_ok=False))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["tok_ok"] = ["tok2"]; c["chat"] = [dict(BOT), dict(NEWBOT)]
    await pg.evaluate("window.__tok[0] = 'tok2'; 0"); await pg.wait_for_timeout(1200)
    v = await pg.evaluate("({txt: document.getElementById('main').innerText, login: window.__login.length, tok: TOKEN, hd: %s})" % HD)
    await done(p, b)
    return "雲端新來的一則" in v["txt"] and v["login"] == 0 and v["tok"] == "tok2" and "⚠️" not in v["hd"], v


async def t67():   # 回到前景立刻讀（舊頁要再等最多 30 秒）——不縮輪詢間隔
    c, h = _cloud()
    p, b, pg, log = await page_with(h, init=HIDDEN)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("window.dispatchEvent(new Event('focus')); 0"); await pg.wait_for_timeout(300)   # 桌機切回來時 focus 常先到、剛讀過一次
    await pg.evaluate("__setHidden(true); 0"); await pg.wait_for_timeout(300)
    c["chat"] = [dict(BOT), dict(NEWBOT)]
    await pg.evaluate("__setHidden(false); 0"); await pg.wait_for_timeout(1500)
    txt = await pg.evaluate("document.getElementById('main').innerText")
    await done(p, b)
    return "雲端新來的一則" in txt, txt[-80:]


async def t68():   # 第一次讀不到（連線失敗）⇒ 畫面照實說、5 秒後自己重試、之後照常輪詢（舊頁停在錯誤字）
    c, h = _cloud(); c["mode"] = "ABORT"
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    first = await pg.evaluate("document.getElementById('main').innerText")
    c["mode"] = "ok"; await pg.wait_for_timeout(5500)
    v = await pg.evaluate("({txt: document.getElementById('main').innerText, hd: %s})" % HD)
    await done(p, b)
    return ("連不上雲端" in first and "自動重試" in first and "好的" in v["txt"] and "已同步" in v["hd"]), (first, v)


async def t69():   # 頁尾一直講實話：正常時「幾點已同步」；讀不到超過 60 秒變警示＋原因；點一下立即重試、恢復後警示消失
    c, h = _cloud()
    p, b, pg, log = await page_with(h, fast=True)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    ok1 = await pg.evaluate(HD)
    c["mode"] = "ABORT"; await pg.wait_for_timeout(400)
    v = await pg.evaluate("() => { SYNCST.okMono = mono() - 120000; SYNCST.resumeMono = SYNCST.okMono; paintSync();"
                          " return {t: document.getElementById('hd').innerText, c: document.getElementById('hd').className}; }")
    c["mode"] = "ok"; await pg.click("#hd"); await pg.wait_for_timeout(600)
    v2 = await pg.evaluate("({t: %s, c: document.getElementById('hd').className})" % HD)
    await done(p, b)
    return ("已同步" in ok1 and "沒同步到雲端" in v["t"] and "連線失敗" in v["t"] and v["c"] == "warn"
            and "已同步" in v2["t"] and v2["c"] == ""), (ok1, v, v2)


async def t70():   # 看門狗：輪詢鏈斷掉（計時器沒了）⇒ 15 秒內自己接回
    c, h = _cloud()
    p, b, pg, log = await page_with(h)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    before = len(log)
    await pg.evaluate("clearTimeout(POLL); window.POLL_DUE = mono() - 20000; 0")
    await pg.wait_for_timeout(6000)
    after = len(log)
    await done(p, b)
    return after > before, (before, after)


async def t71():   # 等回覆時（只抓對話）每 30 秒仍讀一次完整狀態：待回草稿、巡查時間不會整段凍住
    pend = {"id": "mq1", "role": "me", "text": "還在等的題", "status": "pending", "at": "2026-10-24T10:00:00"}
    c, h = _cloud(chat=[dict(BOT), pend])
    p, b, pg, log = await page_with(h, fast=True)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    mark = len(log)
    await pg.evaluate("if (window.SYNCST) SYNCST.stateMono = mono() - 31000; 0"); await pg.wait_for_timeout(800)
    ops = [x.get("op") for x in log[mark:]]
    await done(p, b)
    return "state" in ops and "chat" in ops, ops[:12]


async def t72():   # 停在最底下時，輪詢（完整狀態那條）帶來新訊息要捲進畫面——不然看起來像沒更新
    many = [{"id": "h%d" % i, "role": "bot", "text": "舊訊息 %d" % i, "at": "2026-10-24T09:%02d:00" % (i % 60)} for i in range(40)]
    c, h = _cloud(chat=many)
    p, b, pg, log = await page_with(h, fast=True)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(1200)
    d0 = await pg.evaluate("document.body.scrollHeight - (scrollY + innerHeight)")
    c["chat"] = many + [dict(NEWBOT, text="新的一則\n" + "很長的內容\n" * 30)]
    await pg.wait_for_timeout(2000)
    d1 = await pg.evaluate("document.body.scrollHeight - (scrollY + innerHeight)")
    await done(p, b)
    return d0 < 200 and d1 < 120, (d0, d1)


def _tpe_now():
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")


async def t73():   # #4492②：錄製中等回覆時追加讀的 state 也要收進度（Codex 審 47fbc6f T04：chat 缺 work 記失敗、state 帶 seq=11，舊頁停在 seq=10）
    import time
    now_ms = int(time.time() * 1000)
    pend = {"id": "mq1", "role": "me", "text": "還在等的題", "status": "pending", "at": "2026-10-24T10:00:00"}
    n = {"state": 0}

    async def h(q, log):
        op = q.get("op")
        if op == "state":
            n["state"] += 1
            r = dict(STATE, chat=[dict(BOT), pend], teach={"sid": "s1", "state": "recording", "at": _tpe_now()})
            r["work"] = _work(now_ms, seq=10 if n["state"] == 1 else 11)
            return r
        if op == "chat":
            return {"ok": True, "chat": [dict(BOT), pend]}   # 缺 work 欄＝這條讀取不可信
        return await base(q, log)
    p, b, pg, log = await page_with(h, fast=True)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(2500)
    v = await pg.evaluate("({seq: WORK && WORK.seq, fail: WORK_FAIL_AT, live: !!teachLive()})")
    await done(p, b)
    return v["seq"] == 11 and v["fail"] == 0 and v["live"], v


async def t74():   # #4492③：缺 tries／max_tries／timeout_min／idle_s／ahead ⇒ 寫「不明」，不省略、不冒充「剛開工／下一件就是它」
    import time
    now_ms = int(time.time() * 1000)
    w = {"at": "x", "rx": now_ms - 5000, "now": now_ms, "seq": now_ms, "items": [
        {"id": "w1", "text": "缺欄的那張", "status": "running", "elapsed_s": 600, "steps": 12},
        {"id": "w2", "text": "排隊缺 ahead", "status": "queued"}]}
    w0 = {"at": "x", "rx": now_ms - 5000, "now": now_ms, "seq": now_ms + 1, "items": [
        {"id": "w1", "text": "真的剛開工", "status": "running", "tries": 1, "max_tries": 1, "elapsed_s": 5, "steps": 0},
        {"id": "w2", "text": "只有一次但缺 tries", "status": "running", "max_tries": 1, "elapsed_s": 5, "steps": 0}]}
    p, b, pg = await _wk_page(None)
    t1 = await pg.evaluate("a => { workObserve({ok: true, work: a}); return %s; }" % WK_TXT, w)
    t2 = await pg.evaluate("a => { workObserve({ok: true, work: a}); return %s; }" % WK_TXT, w0)
    await done(p, b)
    ok1 = ("第幾次不明" in t1 and "每輪上限不明" in t1 and "已做 12 個動作・最後動作時間不明" in t1 and "前面還有幾件不明" in t1
           and "剛開工" not in t1 and "下一件就是它" not in t1)
    first2, second2 = (t2.split("只有一次但缺 tries") + [""])[:2]
    ok2 = ("剛開工，還沒看到動作" in first2 and "第幾次" not in first2 and "（每輪上限不明）" in first2
           and "第幾次不明，最多 1 次" in second2)   # Codex 審 f7c7146：max=1 也不保證 tries=1，缺就寫不明
    return ok1 and ok2, (t1, t2)


async def t75():   # 重新登入回來：跳轉前沒送出的字與引用放回輸入框
    init = "try{sessionStorage.setItem('cb_keep', JSON.stringify({t:'沒送出的字', q:'引用那句'}));}catch(e){}"
    c, h = _cloud()
    p, b, pg, log = await page_with(h, init=init)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(1000)
    v = await pg.evaluate("({q: document.getElementById('q').value, r: REPLY, left: sessionStorage.getItem('cb_keep')})")
    await done(p, b)
    return v["q"] == "沒送出的字" and v["r"] == "引用那句" and v["left"] is None, v


async def t76():   # 登入過期但有一則還沒確認收到 ⇒ 不自動跳轉（免得那則不見）；頁尾請律師點，點了才登入、那則的字帶過去
    c, h = _cloud()
    p, b, pg, log = await page_with(h, liff=liff_js(profile_ok=False))   # 正常間隔：送出前不會先有一輪輪詢撞到 forbidden
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["mode"] = "forbidden"
    await pg.fill("#q", "要送的字"); await pg.click("#send"); await pg.wait_for_timeout(1500)
    v = await pg.evaluate("({login: window.__login.length, hd: %s, st: UNSENT.map(function(x){return x.state})})" % HD)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    v2 = await pg.evaluate("({login: window.__login.length, env: JSON.parse(sessionStorage.getItem('cb_carry_v1') || 'null')})")
    await done(p, b)
    env = v2["env"] or {"msgs": []}
    return (v["login"] == 0 and "點這裡重新登入" in v["hd"] and v2["login"] == 1
            and [m["snapshot"]["rawText"] for m in env["msgs"]] == ["要送的字"]), (v, v2)


async def t77():   # LINE 認 token、但雲端回 forbidden（雲端驗證暫時失敗）⇒ 不跳轉、照實標示、照常重試；恢復後警示消失
    c, h = _cloud()
    p, b, pg, log = await page_with(h, fast=True, liff=liff_js(profile_ok=True))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["mode"] = "forbidden"; await pg.wait_for_timeout(800)
    v = await pg.evaluate("({login: window.__login.length, hd: %s})" % HD)
    c["mode"] = "ok"; c["chat"] = [dict(BOT), dict(NEWBOT)]; await pg.wait_for_timeout(800)
    v2 = await pg.evaluate("({hd: %s, txt: document.getElementById('main').innerText})" % HD)
    await done(p, b)
    return (v["login"] == 0 and "雲端不認這次登入" in v["hd"] and "⚠️" not in v2["hd"] and "已同步" in v2["hd"]
            and "雲端新來的一則" in v2["txt"]), (v, v2)



async def t78():   # 「停了沒」單一判準：條變灰 ⇔ 文字說可能卡住；等審查 3–6 分鐘的正常等待不得變灰（10-25 律師截圖問「當機了嗎」）
    import time
    now_ms = int(time.time() * 1000)
    p, b, pg = await _wk_page(None)
    rows = []
    for i, idle in enumerate((30, 130, 200, 330, 470, 490, 600, 1200)):
        w = _work(now_ms, rx_ago=0, idle_s=idle, seq=now_ms + 10 + i)
        w["items"] = w["items"][:1]
        v = await pg.evaluate("""a => { workObserve({ok: true, work: a});
          return {cls: document.querySelector('.wk .pb i').className, warn: %s.indexOf('可能卡住了') >= 0}; }""" % WK_TXT, w)
        rows.append((idle, v["cls"], v["warn"]))
    await done(p, b)
    same = all((c == "idle") == wv for _, c, wv in rows)
    normal_wait_runs = all(c == "run" for i, c, _ in rows if i <= 470)
    return same and normal_wait_runs and rows[-1][1] == "idle", rows


async def t79():   # 還沒載入過就登入過期、且 10 分鐘內剛重登過（防無限跳轉）⇒ 主畫面與頁尾直接寫「點這一行重新登入」、頁尾可點；點了才登入
    init = "try{localStorage.setItem('cb_relogin_at', String(Date.now()));}catch(e){}"
    c, h = _cloud(); c["mode"] = "forbidden"
    p, b, pg, log = await page_with(h, init=init, liff=liff_js(profile_ok=False))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(1500)
    v = await pg.evaluate("({main: document.getElementById('main').innerText, hd: %s, cls: document.getElementById('hd').className,"
                          " login: window.__login.length})" % HD)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    n = await pg.evaluate("window.__login.length")
    await done(p, b)
    return ("點這一行重新登入" in v["main"] and "點這一行重新登入" in v["hd"] and v["cls"] == "warn" and v["login"] == 0 and n == 1), (v, n)


async def t80():   # Codex 審 bf84905 P2：等 LINE 回覆（getProfile）期間另一輪已讀成功 ⇒ 晚到的「token 壞了」不得把人登出
    c, h = _cloud()
    lj = liff_js(profile_ok=False).replace("getProfile:()=>Promise.reject(new Error('401'))",
                                           "getProfile:()=>new Promise(function(res,rej){window.__rejProfile=rej;})")
    p, b, pg, log = await page_with(h, fast=True, liff=lj)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["mode"] = "forbidden"; await pg.wait_for_timeout(300)
    c["mode"] = "ok"; await pg.wait_for_timeout(600)   # 這段期間有一輪讀成功
    await pg.evaluate("window.__rejProfile && window.__rejProfile(new Error('401')); 0"); await pg.wait_for_timeout(500)
    v = await pg.evaluate("({login: window.__login.length, logout: window.__logout, hd: %s, had: !!window.__rejProfile})" % HD)
    await done(p, b)
    return v["had"] and v["login"] == 0 and v["logout"] == 0 and "已同步" in v["hd"], v


async def t81():   # Codex 審 bf84905 P1：重登回來、第一次載入完成前就打了字 ⇒ 舊稿不得遺失（兩份都在輸入框）
    init = "try{sessionStorage.setItem('cb_keep', JSON.stringify({t:'舊稿', q:null}));}catch(e){}"
    c, h = _cloud(); c["mode"] = "ABORT"   # 第一次載入失敗、5 秒後才重試：律師在這段空檔接著打字
    p, b, pg, log = await page_with(h, init=init)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(600)
    early = await pg.input_value("#q")   # 設計者 T01：舊稿在等雲端之前就放回
    await pg.focus("#q"); await pg.keyboard.press("End"); await pg.keyboard.type("新打的"); c["mode"] = "ok"; await pg.wait_for_timeout(5500)
    v = await pg.evaluate("({q: document.getElementById('q').value, left: sessionStorage.getItem('cb_keep')})")
    await done(p, b)
    return early == "舊稿" and v["q"] == "舊稿新打的" and v["left"] is None, (early, v)


async def t82():   # Codex 審 bf84905 P1：手動點重登時，還沒確認送到的那則＋輸入框的下一則都要帶過去
    c, h = _cloud()
    p, b, pg, log = await page_with(h, liff=liff_js(profile_ok=False))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["mode"] = "forbidden"
    await pg.fill("#q", "第一則"); await pg.click("#send"); await pg.wait_for_timeout(1200)
    await pg.fill("#q", "第二則"); await pg.wait_for_timeout(200)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    v = await pg.evaluate("({login: window.__login.length, env: JSON.parse(sessionStorage.getItem('cb_carry_v1') || 'null')})")
    await done(p, b)
    env = v["env"] or {"msgs": [], "composer": {}}
    return (v["login"] == 1 and [m["snapshot"]["rawText"] for m in env["msgs"]] == ["第一則"]
            and env["composer"].get("rawText") == "第二則"), v


async def t83():   # Codex 審 bf84905 P2：等回覆＋正在編輯別的文字框 ⇒ 每 30 秒仍讀完整狀態（收資料不重畫，打到一半的框不被換掉）
    pend = {"id": "mq1", "role": "me", "text": "還在等的題", "status": "pending", "at": "2026-10-24T10:00:00"}
    st2 = {"day": "2026-10-24", "rounds": {}, "pushedAt": "2026-10-25T02:30:00"}
    n = {"state": 0}

    async def h(q, log):
        if q.get("op") == "state":
            n["state"] += 1
            return dict(STATE, chat=[dict(BOT), pend], state=(st2 if n["state"] > 1 else STATE["state"]))
        return await base(q, log, chat=[dict(BOT), pend])
    p, b, pg, log = await page_with(h, fast=True)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("() => { const t = document.createElement('textarea'); t.id = 'probeEdit'; t.value = '打到一半'; document.body.appendChild(t); t.focus(); }")
    mark = len(log)
    await pg.evaluate("if (window.SYNCST) SYNCST.stateMono = mono() - 31000; 0"); await pg.wait_for_timeout(800)
    ops = [x.get("op") for x in log[mark:]]
    v = await pg.evaluate("({pushed: DATA.state && DATA.state.pushedAt, ed: editing(), val: document.getElementById('probeEdit').value,"
                          " hd: %s})" % HD)
    await done(p, b)
    return ("state" in ops and v["pushed"] == "2026-10-25T02:30:00" and v["ed"] and v["val"] == "打到一半"
            and "10-25 02:30" in v["hd"]), (ops[:8], v)


async def t84():   # Codex 審 f7c7146 P2：未確認的那則已「放回輸入框」再手動重登 ⇒ 只帶一份，不重複
    c, h = _cloud()
    p, b, pg, log = await page_with(h, liff=liff_js(profile_ok=False))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["mode"] = "forbidden"
    await pg.fill("#q", "不要重複這則"); await pg.click("#send"); await pg.wait_for_timeout(1200)
    await pg.locator("button", has_text="放回輸入框").first.click(); await pg.wait_for_timeout(200)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    v = await pg.evaluate("({login: window.__login.length, env: JSON.parse(sessionStorage.getItem('cb_carry_v1') || 'null')})")
    await done(p, b)
    env = v["env"] or {"msgs": [], "composer": {}}
    return (v["login"] == 1 and len(env["msgs"]) == 1 and env["composer"].get("originMid") == env["msgs"][0]["mid"]
            and env["composer"].get("rawText") == "不要重複這則"), v


async def t85():   # Codex 審 f7c7146 P2：編輯草稿中輪詢換了 DATA（不重畫）⇒ 按舊卡片的「不用回」仍要記到目前資料、重畫後顯示已標記
    pend = {"id": "mq1", "role": "me", "text": "還在等的題", "status": "pending", "at": "2026-10-24T10:00:00"}
    dec = {}

    def st():
        d = {"card_id": "c1", "chat": "甲", "draft": "稿"}
        if "c1" in dec:
            d["decision"] = dec["c1"]
        return {"day": "2026-10-24", "rounds": {"10:00": {"at": "2026-10-24T10:00:00", "result": "done", "drafts": [d]}}}

    async def h(q, log):
        op = q.get("op")
        if op == "decide":
            dec[q["card"]] = q["decision"]
            return {"ok": True, "recorded": {"decision": q["decision"], "at": "2026-10-25T03:00:00"}}
        if op == "state":
            return dict(STATE, chat=[dict(BOT), pend], state=st())
        return await base(q, log, chat=[dict(BOT), pend])
    p, b, pg, log = await page_with(h, fast=True)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.evaluate("() => { window.__d0 = DATA; document.querySelector('#main textarea').focus(); SYNCST.stateMono = mono() - 31000; }")
    await pg.wait_for_timeout(600)
    replaced = await pg.evaluate("DATA !== window.__d0 && editing()")
    await pg.locator("button", has_text="不用回").first.click(); await pg.wait_for_timeout(300)
    v = await pg.evaluate("({cur: Object.keys(DATA.state.rounds).map(function(k){return DATA.state.rounds[k].drafts||[];}).reduce(function(a,x){return a.concat(x);},[])"
                          ".filter(function(z){return z.card_id==='c1';}).map(function(z){return z.decision;}), txt: document.getElementById('main').innerText})")
    await done(p, b)
    return replaced and v["cur"] == ["no"] and "標記為不用回" in v["txt"], (replaced, v["cur"], v["txt"][-60:])


# ── 設計者 task-mvmrnjsp-7kepqi 方案 A：重新登入跳轉＝原樣暫存訊息模型、回來原樣還原（身分 mid／cid 不變）──
BADS = "Object.keys(sessionStorage).filter(function(k){ return k.indexOf('cb_carry_bad_') === 0; }).map(function(k){ return sessionStorage.getItem(k); })"
MODEL = """() => ({msgs: msgList().map(function(m){ return {mid: m.mid, status: m.status, text: m.snapshot.rawText,
  quote: m.snapshot.quote || null, cids: m.attempts.map(function(a){ return a.cid; }), putBack: !!m.putBack, asset: !!m.snapshot.asset}; }),
  comp: {raw: COMPOSER.rawText, quote: COMPOSER.quote || null, origin: COMPOSER.originMid || null, asset: !!COMPOSER.asset},
  q: document.getElementById('q').value, unsent: UNSENT.length})"""


def _asks(log):
    return [x for x in log if x.get("op") == "ask"]


async def _expired_page(liff=None, **kw):
    """登入過期（雲端一律 forbidden）的頁面；回 (c, p, b, pg, log)。"""
    c, h = _cloud()
    p, b, pg, log = await page_with(h, liff=liff or liff_js(profile_ok=False), **kw)
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["mode"] = "forbidden"
    return c, p, b, pg, log


async def _send(pg, text):
    await pg.fill("#q", text); await pg.click("#send"); await pg.wait_for_timeout(900)


async def _relogin_and_back(pg, c):
    """先讓頁面讀一次撞到 forbidden（頁尾變「點這裡重新登入」）→ 點頁尾手動重登 → 確認有跳 → 雲端恢復 → 模擬回站（真的重新載入整頁）。"""
    await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(500)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    went = await pg.evaluate("window.__login.length")
    c["mode"] = "ok"
    await pg.reload(); await pg.wait_for_timeout(1300)
    return went


async def t86():   # T02／T04／T16：兩則不同 mid 同文字＋下一則草稿（含引用）⇒ 回來後身分、cid、各自內容原樣；新送的 mid 不撞舊的
    c, p, b, pg, log = await _expired_page()
    await _send(pg, "同一句"); await _send(pg, "同一句")
    await pg.fill("#q", "第二則草稿"); await pg.evaluate("setReply('引用乙'); 0")
    before = await pg.evaluate(MODEL)
    went = await _relogin_and_back(pg, c)
    after = await pg.evaluate(MODEL)
    await _send(pg, "新的一則")
    mids = await pg.evaluate("msgList().map(function(m){ return m.mid; })")
    await done(p, b)
    keep = lambda m: [(x["mid"], x["text"], x["cids"], x["status"]) for x in m["msgs"]]
    return (went == 1 and len(before["msgs"]) == 2 and keep(after) == keep(before)
            and after["comp"]["raw"] == "第二則草稿" and after["comp"]["quote"] == "引用乙" and after["comp"]["origin"] is None
            and len(set(mids)) == len(mids) == 3), (before, after, mids)


async def t87():   # T03／T07：放回輸入框（原樣）→ 重登回來 → 原樣送出＝同一則重試、沿用同一個 cid，不新增訊息
    c, p, b, pg, log = await _expired_page()
    await _send(pg, "原樣這則")
    cid0 = (await pg.evaluate(MODEL))["msgs"][0]["cids"][0]
    await pg.locator("button", has_text="放回輸入框").first.click(); await pg.wait_for_timeout(200)
    await _relogin_and_back(pg, c)
    after = await pg.evaluate(MODEL)
    n0 = len(_asks(log))
    await pg.click("#send"); await pg.wait_for_timeout(900)
    asks = _asks(log)[n0:]
    v = await pg.evaluate(MODEL)
    await done(p, b)
    return (len(after["msgs"]) == 1 and after["comp"]["origin"] == after["msgs"][0]["mid"] and after["msgs"][0]["putBack"]
            and len(asks) == 1 and asks[0]["cid"] == cid0 and len(v["msgs"]) == 1), (after, [a["cid"] for a in asks], cid0)


async def t88():   # T05：放回後改寫 → 重登回來 ⇒ 原文定格不變、改稿與來源關聯都在；原訊息未確認前改稿不送
    c, p, b, pg, log = await _expired_page()
    await _send(pg, "原文")
    await pg.locator("button", has_text="放回輸入框").first.click(); await pg.wait_for_timeout(200)
    await pg.fill("#q", "改過的")
    await _relogin_and_back(pg, c)
    after = await pg.evaluate(MODEL)
    n0 = len(_asks(log))
    await pg.click("#send"); await pg.wait_for_timeout(600)
    n1 = len(_asks(log))
    await done(p, b)
    return (len(after["msgs"]) == 1 and after["msgs"][0]["text"] == "原文" and after["comp"]["raw"] == "改過的"
            and after["comp"]["origin"] == after["msgs"][0]["mid"] and n1 == n0), (after, n0, n1)


async def t89():   # T06：未確認一則＋輸入框打了一樣的字（沒放回）⇒ 兩者都在，草稿不被誤綁來源
    c, p, b, pg, log = await _expired_page()
    await _send(pg, "一樣的字")
    await pg.fill("#q", "一樣的字")
    await _relogin_and_back(pg, c)
    after = await pg.evaluate(MODEL)
    await done(p, b)
    return (len(after["msgs"]) == 1 and after["msgs"][0]["text"] == "一樣的字" and after["comp"]["raw"] == "一樣的字"
            and after["comp"]["origin"] is None), after


async def t90():   # T08：回站第一次讀到的雲端對話已有那則的 cid ⇒ 首次載入就認成已收到、待處理卡消失、原樣不再送
    c, p, b, pg, log = await _expired_page()
    await _send(pg, "雲端其實收到")
    cid0 = (await pg.evaluate(MODEL))["msgs"][0]["cids"][0]
    c["chat"] = [dict(BOT), {"id": "m" + cid0, "role": "me", "text": "雲端其實收到", "at": "2026-10-24T10:02:00"}]
    await _relogin_and_back(pg, c)
    after = await pg.evaluate(MODEL)
    await done(p, b)
    return (after["msgs"] and after["msgs"][0]["status"] == "accepted" and after["unsent"] == 0), after


async def t91():   # T13：一則正在送／圖還在處理／正在選字 ⇒ 手動也不登出、不跳轉，頁尾講原因
    out = []
    for setup, word in (("ACTIVE = 99", "正在送"), ("COMPOSER.imageJobId = 5", "圖還在處理"), ("COMPOSING = true", "正在選字")):
        c, p, b, pg, log = await _expired_page()
        await _send(pg, "未確認的一則")
        await pg.evaluate(setup + "; relogin(true); 0"); await pg.wait_for_timeout(200)
        v = await pg.evaluate("({login: window.__login.length, logout: window.__logout, hd: %s})" % HD)
        await done(p, b)
        out.append((word, v["login"], v["logout"], word in v["hd"]))
    return all(l == 0 and lo == 0 and hit for _, l, lo, hit in out), out


async def t92():   # T14：暫存寫不進去（sessionStorage 拋錯）⇒ 不登出、不跳轉，頁尾講暫存不下
    c, p, b, pg, log = await _expired_page()
    await _send(pg, "存不下的這則")
    await pg.evaluate("Storage.prototype.setItem = function(){ throw new Error('quota'); }; 0")
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    v = await pg.evaluate("({login: window.__login.length, logout: window.__logout, hd: %s, n: msgList().length})" % HD)
    await done(p, b)
    return v["login"] == 0 and v["logout"] == 0 and "暫存不下" in v["hd"] and v["n"] == 1, v


async def t93():   # T15：回站後第一次載入失敗→律師改了草稿→重試成功 ⇒ 舊暫存不再插入；暫存只用一次
    c, p, b, pg, log = await _expired_page()
    await pg.fill("#q", "原草稿")
    await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(500)
    hold = await pg.evaluate(HD)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    c["mode"] = "ABORT"
    await pg.reload(); await pg.wait_for_timeout(800)
    early = await pg.input_value("#q")
    await pg.fill("#q", "改成這樣"); c["mode"] = "ok"; await pg.wait_for_timeout(5500)
    v = await pg.evaluate("({q: document.getElementById('q').value, left: sessionStorage.getItem('cb_carry_v1'), d: !!DATA})")
    await done(p, b)
    return "點這裡重新登入" in hold and early == "原草稿" and v["q"] == "改成這樣" and v["left"] is None and v["d"], (hold, early, v)


async def t94():   # T17：只有空白／只有引用 ⇒ 也算內容，原樣帶回（不被 trim 濾掉）
    out = []
    for setup in ("Q.value = '   '; Q.dispatchEvent(new Event('input'))", "setReply('只有引用')"):
        c, p, b, pg, log = await _expired_page()
        await pg.evaluate(setup + "; 0")
        before = (await pg.evaluate(MODEL))["comp"]
        await _relogin_and_back(pg, c)
        after = (await pg.evaluate(MODEL))["comp"]
        await done(p, b)
        out.append((before, after))
    return all(a["raw"] == bf["raw"] and a["quote"] == bf["quote"] for bf, a in out) and out[1][1]["quote"] == "只有引用", out


async def t95():   # T18：暫存紀錄壞掉（不是 JSON）⇒ 不部分還原、原紀錄搬到隔離鍵保存、不自動送出；之後自動重登也不會刪掉它（Codex 審 0537fb5 P2）
    seed = "if(!sessionStorage.getItem('__seeded')){ sessionStorage.setItem('__seeded','1'); sessionStorage.setItem('cb_carry_v1','{bad'); }"
    c, h = _cloud()
    p, b, pg, log = await page_with(h, init=seed, liff=liff_js(profile_ok=False))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(1000)
    v = await pg.evaluate("({n: msgList().length, bad: %s, d: !!DATA, q: document.getElementById('q').value})" % BADS)
    c["mode"] = "forbidden"; await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(600)   # 空模型＋登入過期 ⇒ 自動重登
    v2 = await pg.evaluate("({login: window.__login.length, bad: %s})" % BADS)
    await done(p, b)
    return (v["n"] == 0 and v["bad"] == ["{bad"] and v["d"] and v["q"] == "" and not _asks(log)
            and v2["login"] == 1 and v2["bad"] == ["{bad"]), (v, v2)


async def t96():   # T11／T12：附圖那則送不成→放回→重登回來 ⇒ 圖原樣（同一個圖物件＝判為原樣）、原樣送出沿用 cid 與已上傳的附件、不重傳圖
    c, h = _cloud()
    p, b, pg, log, ups = await mk(h, liff=liff_js(profile_ok=False))
    c["mode"] = "forbidden"
    await pg.evaluate("setAtt({url:'data:image/jpeg;base64,/9j/AA==', b64:'/9j/AA==', mime:'image/jpeg'}); Q.value='附圖這則'; fitQ(); SEND.onclick(); 0")
    await pg.wait_for_timeout(900)
    cid0 = (await pg.evaluate(MODEL))["msgs"][0]["cids"][0]
    await pg.locator("button", has_text="放回輸入框").first.click(); await pg.wait_for_timeout(200)
    await _relogin_and_back(pg, c)
    same = await pg.evaluate("(function(){ var m = msgList()[0]; return !!m && COMPOSER.asset === m.snapshot.asset && m.snapshot.asset.b64 === '/9j/AA=='; })()")
    nup = len(ups); n0 = len(_asks(log))
    await pg.click("#send"); await pg.wait_for_timeout(900)
    asks = _asks(log)[n0:]
    await done(p, b)
    return same and len(asks) == 1 and asks[0]["cid"] == cid0 and asks[0].get("att") == "att1" and len(ups) == nup == 1, (same, asks, nup, len(ups))


async def t97():   # T19：LINE 內建瀏覽器走 logout＋reload ⇒ 同一套暫存／還原，回來訊息身分原樣、不循環跳轉
    lj = liff_js(profile_ok=False).replace("isInClient:()=>false", "isInClient:()=>true")
    c, p, b, pg, log = await _expired_page(liff=lj)
    await _send(pg, "內建瀏覽器這則")
    before = await pg.evaluate(MODEL)
    async with pg.expect_navigation():
        c["mode"] = "ok"
        await pg.click("#hd")
    await pg.wait_for_timeout(1300)
    after = await pg.evaluate(MODEL)
    await pg.wait_for_timeout(1500)
    url_ok = await pg.evaluate("location.href")
    await done(p, b)
    k = lambda m: [(x["mid"], x["text"], x["cids"]) for x in m["msgs"]]
    return k(after) == k(before) and len(before["msgs"]) == 1 and url_ok.startswith("https://x.test/"), (before, after)


async def t98():   # Codex 審 0537fb5 P1：回站還原後 LINE 說未登入 ⇒ 跳去登入前把剛還原的訊息再存一次（不然下一頁沒東西可還原）
    lj = liff_js(profile_ok=False).replace("isLoggedIn:()=>true", "isLoggedIn:()=>sessionStorage.getItem('__nologin')!=='1'")
    c, p, b, pg, log = await _expired_page(liff=lj)
    await _send(pg, "要撐過兩次跳轉")
    await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(500)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    await pg.evaluate("sessionStorage.setItem('__nologin','1'); 0")
    await pg.reload(); await pg.wait_for_timeout(800)
    v = await pg.evaluate("({login: window.__login, env: JSON.parse(sessionStorage.getItem('cb_carry_v1') || 'null')})")
    await done(p, b)
    env = v["env"] or {"msgs": []}
    return ([m["snapshot"]["rawText"] for m in env["msgs"]] == ["要撐過兩次跳轉"] and len(v["login"]) == 1
            and v["login"][0].get("redirectUri") == "https://x.test/"), v


async def t99():   # Codex 審 0537fb5 P1：紀錄裡的計數器過舊（msgn:0）⇒ 新送的 mid 仍不得蓋掉還原回來的那則
    c, p, b, pg, log = await _expired_page()
    await _send(pg, "還原回來的第一則")
    await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(500)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    await pg.evaluate("(function(){ var e = JSON.parse(sessionStorage.getItem('cb_carry_v1')); e.counters.msgn = 0; e.counters.attseq = 0; sessionStorage.setItem('cb_carry_v1', JSON.stringify(e)); })()")
    c["mode"] = "ok"
    await pg.reload(); await pg.wait_for_timeout(1300)
    await _send(pg, "新的第二則")
    v = await pg.evaluate("msgList().map(function(m){ return [m.mid, m.snapshot.rawText, m.attempts.map(function(a){ return a.attemptId; })]; })")
    await done(p, b)
    mids = [x[0] for x in v]; atts = [a for x in v for a in x[2]]
    return len(v) == 2 and len(set(mids)) == 2 and len(set(atts)) == len(atts) and "還原回來的第一則" in [x[1] for x in v], v


async def t100():   # Codex 審 0537fb5 P2：狀態被竄改（"corrupt"）或計數器缺欄 ⇒ 整份不還原、搬到隔離鍵、不送任何東西
    out = []
    for mut in ("e.msgs[0].status = 'corrupt'", "e.counters = {}"):
        c, p, b, pg, log = await _expired_page()
        await _send(pg, "會被竄改的紀錄")
        await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(500)
        await pg.click("#hd"); await pg.wait_for_timeout(300)
        await pg.evaluate("(function(){ var e = JSON.parse(sessionStorage.getItem('cb_carry_v1')); %s; sessionStorage.setItem('cb_carry_v1', JSON.stringify(e)); })()" % mut)
        c["mode"] = "ok"; n0 = len(_asks(log))
        await pg.reload(); await pg.wait_for_timeout(1300)
        v = await pg.evaluate("({n: msgList().length, main: sessionStorage.getItem('cb_carry_v1'), bad: %s.length === 1, q: document.getElementById('q').value})" % BADS)
        await done(p, b)
        out.append((mut, v, len(_asks(log)) - n0))
    return all(v["n"] == 0 and v["main"] is None and v["bad"] and v["q"] == "" and k == 0 for _, v, k in out), out


async def _carried_then(pg, c, text, mut=None):
    """送一則未確認 → 撞 forbidden → 手動重登暫存 → （可選）改暫存內容 → 雲端恢復。回傳前不重載。"""
    await _send(pg, text)
    await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(500)
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    if mut:
        await pg.evaluate("(function(){ var e = JSON.parse(sessionStorage.getItem('cb_carry_v1')); %s; sessionStorage.setItem('cb_carry_v1', JSON.stringify(e)); })()" % mut)
    c["mode"] = "ok"


async def t101():   # 4agent A1／Codex 50f9356 F1：回站還原後 LINE 說未登入、且暫存寫不回去 ⇒ 還原那份仍在（載入成功前不刪），照樣去登入、訊息不丟
    lj = liff_js(profile_ok=False).replace("isLoggedIn:()=>true", "isLoggedIn:()=>sessionStorage.getItem('__nologin')!=='1'")
    failset = ("if(sessionStorage.getItem('__failset')==='1'){ var o = Storage.prototype.setItem;"
               " Storage.prototype.setItem = function(k, v){ if(k === 'cb_carry_v1') throw new Error('quota'); return o.call(this, k, v); }; }")
    c, p, b, pg, log = await _expired_page(liff=lj, init=failset)
    await _carried_then(pg, c, "撐過存不回去")
    await pg.evaluate("sessionStorage.setItem('__nologin','1'); sessionStorage.setItem('__failset','1'); 0")
    await pg.reload(); await pg.wait_for_timeout(800)
    v = await pg.evaluate("({login: window.__login.length, env: JSON.parse(sessionStorage.getItem('cb_carry_v1') || 'null')})")
    await done(p, b)
    env = v["env"] or {"msgs": []}
    return v["login"] == 1 and [m["snapshot"]["rawText"] for m in env["msgs"]] == ["撐過存不回去"], v


async def t102():   # Codex 50f9356 F2／F3：mid 超出本頁格式（l9007199254740992）、status 是陣列 ⇒ 整份不還原、隔離、不送
    out = []
    for mut in ("e.msgs[0].mid = 'l9007199254740992'", "e.msgs[0].status = ['unknown']", "e.msgs[0].status = ['accepted']; e.msgs[0].acceptedCid = null"):
        c, p, b, pg, log = await _expired_page()
        await _carried_then(pg, c, "被改壞的紀錄", mut)
        n0 = len(_asks(log))
        await pg.reload(); await pg.wait_for_timeout(1300)
        v = await pg.evaluate("({n: msgList().length, nb: %s.length})" % BADS)
        await done(p, b)
        out.append((mut, v, len(_asks(log)) - n0))
    return all(v["n"] == 0 and v["nb"] == 1 and k == 0 for _, v, k in out), out


async def t103():   # Codex 50f9356 F4＋4agent A3：連續兩份壞紀錄各自保存；隔離寫不進去也不鎖死登入（空畫面仍能自動重登）
    seed = ("if(!sessionStorage.getItem('__seeded')){ sessionStorage.setItem('__seeded','1'); sessionStorage.setItem('cb_carry_v1','{first'); }"
            "else if(sessionStorage.getItem('__seeded') === '1'){ sessionStorage.setItem('__seeded','2'); sessionStorage.setItem('cb_carry_v1','{second'); }")
    c, h = _cloud()
    p, b, pg, log = await page_with(h, init=seed, liff=liff_js(profile_ok=False))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    await pg.reload(); await pg.wait_for_timeout(800)
    two = await pg.evaluate("%s.sort()" % BADS)
    await done(p, b)
    lock = ("if(!sessionStorage.getItem('__seeded')){ sessionStorage.setItem('__seeded','1'); sessionStorage.setItem('cb_carry_v1','{stuck'); }"
            " var o = Storage.prototype.setItem; Storage.prototype.setItem = function(k, v){ if(k.indexOf('cb_carry_bad_') === 0) throw new Error('quota'); return o.call(this, k, v); };")
    c, h = _cloud()
    p, b, pg, log = await page_with(h, init=lock, liff=liff_js(profile_ok=False))
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(800)
    c["mode"] = "forbidden"; await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(600)
    v = await pg.evaluate("({login: window.__login.length, main: sessionStorage.getItem('cb_carry_v1')})")
    await done(p, b)
    return two == ["{first", "{second"] and v["login"] == 1 and v["main"] == "{stuck", (two, v)


async def t104():   # Codex 50f9356 F5＋4agent A5：在例行任務頁重登 ⇒ redirectUri 不帶 #（RFC 6749 §3.1.2），回來仍在例行任務頁
    c, p, b, pg, log = await _expired_page()
    await pg.evaluate("document.getElementById('tasks').onclick(); 0"); await pg.wait_for_timeout(200)
    await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(600)   # 空畫面 ⇒ 自動重登
    v = await pg.evaluate("({login: window.__login, view: sessionStorage.getItem('cb_view')})")
    c["mode"] = "ok"
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(1200)   # 登入回來的網址不帶 #
    v2 = await pg.evaluate("({view: VIEW, hash: location.hash, left: sessionStorage.getItem('cb_view')})")
    await done(p, b)
    uri = (v["login"] or [{}])[0].get("redirectUri", "")
    return uri == "https://x.test/" and "#" not in uri and v2["view"] == "tasks" and v2["hash"] == "#tasks" and v2["left"] is None, (v, v2)


async def t105():   # 4agent A1：回站還原後第一次載入失敗→整頁重載 ⇒ 暫存還在、再還原一次；第一次載入成功後才刪
    c, p, b, pg, log = await _expired_page()
    await _carried_then(pg, c, "載入失敗也不丟")
    c["mode"] = "ABORT"
    await pg.reload(); await pg.wait_for_timeout(800)
    mid1 = await pg.evaluate("({n: msgList().length, kept: sessionStorage.getItem('cb_carry_v1') !== null})")
    await pg.reload(); await pg.wait_for_timeout(800)
    mid2 = await pg.evaluate("({n: msgList().length})")
    c["mode"] = "ok"; await pg.wait_for_timeout(5500)
    end = await pg.evaluate("({n: msgList().length, kept: sessionStorage.getItem('cb_carry_v1') !== null, d: !!DATA})")
    await done(p, b)
    return mid1 == {"n": 1, "kept": True} and mid2["n"] == 1 and end == {"n": 1, "kept": False, "d": True}, (mid1, mid2, end)


async def t106():   # Codex 審 d974c16 F1：還原後、等 LIFF 期間又打了字，暫存寫不回去且 LINE 說未登入 ⇒ 不跳（新打的字不能丟）；沒改過的 t101 仍會跳
    lj = (liff_js(profile_ok=False).replace("isLoggedIn:()=>true", "isLoggedIn:()=>sessionStorage.getItem('__nologin')!=='1'")
          .replace("init:()=>Promise.resolve()", "init:()=>new Promise(function(r){ setTimeout(r, sessionStorage.getItem('__slow')==='1' ? 1500 : 0); })"))
    failset = ("if(sessionStorage.getItem('__failset')==='1'){ var o = Storage.prototype.setItem;"
               " Storage.prototype.setItem = function(k, v){ if(k === 'cb_carry_v1') throw new Error('quota'); return o.call(this, k, v); }; }")
    c, p, b, pg, log = await _expired_page(liff=lj, init=failset)
    await _carried_then(pg, c, "原本那則")
    await pg.fill("#q", "原草稿")   # 讓還原後輸入框有字（這版暫存在手動重登時寫入）
    await pg.click("#hd"); await pg.wait_for_timeout(300)
    await pg.evaluate("sessionStorage.setItem('__nologin','1'); sessionStorage.setItem('__failset','1'); sessionStorage.setItem('__slow','1'); 0")
    await pg.reload(); await pg.wait_for_timeout(300)
    await pg.focus("#q"); await pg.keyboard.press("End"); await pg.keyboard.type("＋新增重要內容")
    await pg.wait_for_timeout(1800)
    v = await pg.evaluate("({login: window.__login.length, q: document.getElementById('q').value, main: document.getElementById('main').innerText})")
    await done(p, b)
    return v["login"] == 0 and v["q"].endswith("＋新增重要內容") and "暫存不下" in v["main"], v


async def t107():   # Codex 審 d974c16 F5：例行任務頁重登→回站仍未登入→再登入→回站 ⇒ 仍在例行任務頁；未登入直接開 #tasks 也一樣
    lj = liff_js(profile_ok=False).replace("isLoggedIn:()=>true", "isLoggedIn:()=>sessionStorage.getItem('__nologin')!=='1'")
    c, p, b, pg, log = await _expired_page(liff=lj)
    await pg.evaluate("document.getElementById('tasks').onclick(); 0"); await pg.wait_for_timeout(200)
    await pg.evaluate("kick(true); 0"); await pg.wait_for_timeout(600)   # 空畫面 ⇒ 自動重登（第一次跳）
    await pg.evaluate("sessionStorage.setItem('__nologin','1'); 0")
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(600)   # 回站仍未登入 ⇒ 第二次跳
    second = await pg.evaluate("({n: window.__login.length, view: sessionStorage.getItem('cb_view')})")
    await pg.evaluate("sessionStorage.removeItem('__nologin'); 0"); c["mode"] = "ok"
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(1000)
    a = await pg.evaluate("({view: VIEW, hash: location.hash})")
    await done(p, b)
    c, h = _cloud()
    p, b, pg, log = await page_with(h, liff=lj)
    await pg.goto("https://x.test/"); await pg.evaluate("sessionStorage.setItem('__nologin','1'); 0")
    await pg.goto("about:blank"); await pg.goto("https://x.test/#tasks"); await pg.wait_for_timeout(600)   # 未登入直接開 #tasks ⇒ 跳去登入（先離開再回來＝真的重新載入）
    nb = await pg.evaluate("({n: window.__login.length, view: sessionStorage.getItem('cb_view')})")
    await pg.evaluate("sessionStorage.removeItem('__nologin'); 0")
    await pg.goto("https://x.test/"); await pg.wait_for_timeout(1000)   # 回站網址不帶 #
    b2 = await pg.evaluate("({view: VIEW, hash: location.hash})")
    await done(p, b)
    return (second["n"] == 1 and second["view"] == "tasks" and a == {"view": "tasks", "hash": "#tasks"}
            and nb == {"n": 1, "view": "tasks"} and b2 == {"view": "tasks", "hash": "#tasks"}), (second, a, nb, b2)

MATRIX = [t01, t02, t03, t04, t05, t06, t07, t08, t09, t10, t11, t12, t13, t14, t15, t16, t17, t18, t19, t20,
          t21, t22, t23, t24, t25, t26, t27, t28, t29, t30, t31, t32, t33, t34, t35, t36, t37,
          t38, t39, t40, t41, t42, t43, t44, t45, t46, t47, t48, t49, t50, t51, t52, t53, t54, t55, t56, t57, t58, t59, t60, t61, t62, t63, t64,
          t65, t66, t67, t68, t69, t70, t71, t72, t73, t74, t75, t76, t77, t78, t79, t80, t81, t82, t83, t84, t85,
          t86, t87, t88, t89, t90, t91, t92, t93, t94, t95, t96, t97, t98, t99, t100, t101, t102, t103, t104, t105, t106, t107]
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
