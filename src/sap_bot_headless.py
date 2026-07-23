from pathlib import Path
import re
import time

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext, ElementHandle


class SAPBot:
    def __init__(self):
        self._pw               = None
        self._browser: Browser = None
        self._ctx: BrowserContext = None
        self.page: Page        = None
        self.run_started_at    = time.strftime("%Y%m%d_%H%M%S")
        self.screenshot_dir    = Path.cwd() / "screenshots" / self.run_started_at
        self.screenshot_counter = 0
        self.last_screenshot_path: Path | None = None

    # =========================
    # SETUP & LOGIN
    # =========================
    def start(self):
        self._pw      = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ],
        )
        self._ctx  = self._browser.new_context(viewport={"width": 1920, "height": 1080})
        self.page  = self._ctx.new_page()
        self.page.goto("https://agencysvc44.sapsf.com")

    def login(self):
        import os
        from dotenv import load_dotenv
        load_dotenv()

        company_id = os.getenv("SAP_COMPANY_ID")
        agency_id  = os.getenv("SAP_AGENCY_ID")
        email      = os.getenv("SAP_EMAIL")
        password   = os.getenv("SAP_PASSWORD")

        if not all([company_id, agency_id, email, password]):
            raise Exception("Missing SAP credentials — need SAP_COMPANY_ID, SAP_AGENCY_ID, SAP_EMAIL, SAP_PASSWORD")

        print("Step 1: entering Company ID")
        self.page.wait_for_selector('[name="companyId"]', timeout=20000)
        self.page.wait_for_timeout(2000)
        self.page.fill('[name="companyId"]', company_id)
        self.page.click("button[id*='continueButton']")
        self.page.wait_for_timeout(3000)
        self._screenshot("00_company_id_submitted")

        print("Step 2: entering credentials")
        self.page.wait_for_selector("xpath=//input[contains(@placeholder,'Agency')]", timeout=20000)
        self.page.fill("xpath=//input[contains(@placeholder,'Agency')]", agency_id)
        self.page.fill("xpath=//input[contains(@placeholder,'Email')]", email)
        self.page.fill("input[type='password']", password)
        self.page.click("button[id*='login']")
        self.page.wait_for_timeout(5000)

        if "login" in self.page.url.lower():
            raise Exception("Login failed — check SAP credentials")

        print("Logged in successfully")
        self._screenshot("01_logged_in")

    def close(self):
        for attr in ("page", "_ctx", "_browser", "_pw"):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    obj.close() if attr != "_pw" else obj.stop()
                except Exception:
                    pass
                setattr(self, attr, None)

    def quit(self):
        self.close()

    # =========================
    # DIALOG / ERROR HELPERS
    # =========================
    def _is_existing_candidate_dialog(self) -> bool:
        try:
            return bool(self.page.evaluate("""() => {
                function visible(el) {
                    if (!el) return false;
                    var s = window.getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden') return false;
                    var r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }
                var dialogs = Array.from(document.querySelectorAll('.sapMDialog, [role="dialog"]')).filter(visible);
                for (var d of dialogs) {
                    var t = (d.innerText || '').toLowerCase();
                    if (t.indexOf('submit existing candidate') >= 0 || t.indexOf('candidate already exists') >= 0)
                        return true;
                }
                return false;
            }"""))
        except Exception:
            return False

    def _extract_screen_error(self) -> str:
        try:
            text = self.page.evaluate("""() => {
                function visible(el) {
                    if (!el) return false;
                    var s = window.getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden') return false;
                    var r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }
                function clean(t) { return (t || '').replace(/\\s+/g, ' ').trim(); }

                // Strip boilerplate form text that follows the actual error message
                var FORM_NOISE = [
                    'Enter the candidate', 'indicates a mandatory field',
                    'First Name', 'Last Name', 'Email Address', 'Select a resume',
                    'Select File', 'I understand and agree',
                ];
                function stripNoise(t) {
                    for (var marker of FORM_NOISE) {
                        var idx = t.indexOf(marker);
                        if (idx > 0) t = t.slice(0, idx);
                    }
                    return t.trim();
                }

                // 1. Toast messages
                var toasts = Array.from(document.querySelectorAll('.sapMMessageToast')).filter(visible);
                if (toasts.length) return clean(toasts[0].innerText).slice(0, 400);

                // 2. Specific message-strip text spans (most precise)
                var stripTextSelectors = [
                    '.sapMMessageStripMessage',
                    '[class*="MessageStripMessage"]',
                    '.sapMDialog .sapMMsgStripMessage',
                ];
                for (var sel of stripTextSelectors) {
                    var els = Array.from(document.querySelectorAll(sel)).filter(visible);
                    if (els.length) { var t = clean(els[0].innerText); if (t) return t.slice(0, 400); }
                }

                // 3. Message strip containers (error/warning type preferred)
                var allStrips = Array.from(document.querySelectorAll(
                    '.sapMMessageStrip, [class*="sapMMessageStrip"]'
                )).filter(visible);
                // Prefer error strips first
                var errorStrips = allStrips.filter(function(el) {
                    return /Error|Warning/i.test(el.className || '');
                });
                var strips = errorStrips.length ? errorStrips : allStrips;
                for (var ds of strips) {
                    var t = stripNoise(clean(ds.innerText));
                    if (t) return t.slice(0, 400);
                }

                // 4. role="alert" elements
                var alerts = Array.from(document.querySelectorAll('[role="alert"]')).filter(visible);
                for (var a of alerts) {
                    var t = stripNoise(clean(a.innerText));
                    if (t) return t.slice(0, 400);
                }

                // 5. Full dialog fallback — strip noise before returning
                var dialogs = Array.from(document.querySelectorAll('.sapMDialog, [role="alertdialog"]')).filter(visible);
                for (var d of dialogs) {
                    var t = stripNoise(clean(d.innerText));
                    if (t) return t.slice(0, 400);
                }

                return '';
            }""")
            return str(text or "").strip()
        except Exception:
            return ""

    # =========================
    # INTERNAL HELPERS
    # =========================
    def _fill(self, xpath: str, value: str):
        el = self.page.wait_for_selector(f"xpath={xpath}", timeout=20000)
        el.scroll_into_view_if_needed()
        el.click()
        el.evaluate("""(el, val) => {
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeInputValueSetter.call(el, val);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
        }""", value)
        self.page.wait_for_timeout(200)

    def _sap_select(self, control_id: str, value: str, by: str = "key"):
        match_expr  = (f"i.getKey() === '{value}'" if by == "key"
                       else f"i.getText().trim() === '{value}' || i.getText().includes('{value}')")
        select_stmt = (f"c.setSelectedKey('{value}');" if by == "key" else "c.setSelectedItem(match);")
        return self.page.evaluate(f"""() => {{
            try {{
                var c = sap.ui.getCore().byId('{control_id}');
                if (!c) return 'not_found';
                var match = c.getItems().find(i => {match_expr});
                if (!match) return 'item_not_found';
                {select_stmt}
                c.fireChange({{selectedItem: c.getSelectedItem()}});
                return 'ok:' + c.getSelectedItem().getText();
            }} catch(e) {{ return 'error:' + e.message; }}
        }}""")

    def _action_click(self, element: ElementHandle):
        element.scroll_into_view_if_needed()
        self.page.wait_for_timeout(300)
        element.hover()
        element.click()

    def _screenshot(self, name: str) -> Path:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_counter += 1
        path = self.screenshot_dir / f"{self.screenshot_counter:02d}_{name}.png"
        try:
            dialog = self.page.query_selector(".sapMDialog[role='dialog'], .sapMDialog, [role='dialog']")
            if dialog and dialog.is_visible():
                dialog.screenshot(path=str(path))
            else:
                self.page.screenshot(path=str(path))
        except Exception:
            try:
                self.page.screenshot(path=str(path))
            except Exception:
                pass
        print(f"Screenshot saved: {path}")
        self.last_screenshot_path = path
        return path

    def _details_panel_state(self, req_id=None):
        return self.page.evaluate("""(wanted) => {
            var w = wanted ? String(wanted).replace(/\\s+/g, '').toLowerCase() : null;
            function visible(el) {
                if (!el) return false;
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                var rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }
            var nodes = Array.from(document.querySelectorAll('section,div,span,h1,h2,h3,h4,bdi,label'));
            var snippets = [];
            for (var node of nodes) {
                if (!visible(node)) continue;
                if (node.closest('li.sapMLIB')) continue;
                var text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!text || text.length < 8) continue;
                if (text.toLowerCase().includes('requisition id')) {
                    var normalized = text.replace(/\\s+/g, '').toLowerCase();
                    if (w && normalized.includes(w)) return {matched: true, snippet: text.slice(0, 250)};
                    snippets.push(text.slice(0, 250));
                }
            }
            return {matched: false, snippet: snippets[0] || ''};
        }""", req_id)

    def _wait_for_details_panel(self, req_id, timeout=15):
        end         = time.time() + timeout
        last_snippet = ""
        while time.time() < end:
            state        = self._details_panel_state(req_id)
            last_snippet = state.get("snippet", "")
            if state.get("matched"):
                return True, last_snippet
            time.sleep(0.6)
        return False, last_snippet

    def _extract_job_panel_details(self):
        return self.page.evaluate("""() => {
            function visible(el) {
                if (!el) return false;
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                var rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }
            function clean(text) { return (text || '').replace(/\\s+/g, ' ').trim(); }
            function normalizeBlock(text) {
                return String(text || '').replace(/\\r/g,'').replace(/[ \\t]+\\n/g,'\\n').replace(/\\n[ \\t]+/g,'\\n').trim();
            }
            function sanitizePerson(text) {
                return clean(text)
                    .replace(/^(recruiter|client recruiter|agency contact)\\s*:?/i,'')
                    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/ig,' ')
                    .replace(/\\s*copyright.*$/i,'').replace(/\\s*job details.*$/i,'')
                    .replace(/[\\uE000-\\uF8FF]+/g,'').replace(/[^A-Za-z0-9@._+\\-\\s]/g,' ')
                    .replace(/\\s+/g,' ').trim();
            }
            function nextValue(lines, index) {
                for (var j = index + 1; j < lines.length; j++) {
                    var candidate = clean(lines[j]);
                    if (!candidate) continue;
                    if (/^(Requisition ID|Posting Start Date|Posting End Date|Recruiter|Client Recruiter|Agency Contact|Job Details)$/i.test(candidate)) continue;
                    return candidate;
                }
                return '';
            }
            function parseSummaryText(text) {
                var raw = normalizeBlock(text);
                var lines = raw.split(/\\n+/).map(clean).filter(Boolean);
                var flattened = clean(raw.replace(/\\n+/g,' '));
                var data = {title:'',requisition_id:'',posting_start_date:'',posting_end_date:'',recruiter_name:'',recruiter_email:''};
                data.title = lines.find(function(line){
                    return !/^(Requisition ID|Posting Start Date|Posting End Date|Recruiter|Client Recruiter|Agency Contact|Job Details)$/i.test(line);
                }) || '';
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i];
                    var reqMatch = line.match(/^Requisition ID\\s*:?\\s*(.+)$/i);
                    var recruiterMatch = line.match(/^(Recruiter|Client Recruiter|Agency Contact)\\s*:?\\s*(.+)$/i);
                    if (/^Requisition ID$/i.test(line)) data.requisition_id = nextValue(lines, i) || data.requisition_id;
                    else if (reqMatch) data.requisition_id = clean(reqMatch[1]) || data.requisition_id;
                    if (/^(Recruiter|Client Recruiter|Agency Contact)$/i.test(line)) {
                        data.recruiter_name = sanitizePerson(nextValue(lines, i) || data.recruiter_name);
                    } else if (recruiterMatch) {
                        data.recruiter_name = sanitizePerson(recruiterMatch[2] || data.recruiter_name);
                    }
                    var emailMatch = line.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/i);
                    if (emailMatch && !data.recruiter_email) data.recruiter_email = emailMatch[0];
                }
                if (!data.recruiter_name) {
                    var ri = raw.match(/(?:Recruiter|Client Recruiter|Agency Contact)\\s*:?\\s*([^\\n]+)/i);
                    if (ri) data.recruiter_name = sanitizePerson(ri[1]);
                }
                if (!data.title && flattened) {
                    var tm = flattened.match(/^(.*?)\\s+Requisition ID\\b/i);
                    if (tm) data.title = clean(tm[1]);
                }
                return data;
            }
            function summarize(el) {
                var rect = el.getBoundingClientRect();
                var rawText = normalizeBlock(el.innerText);
                return {el:el, text:clean(rawText), rawText:rawText, top:rect.top, left:rect.left, area:rect.width*rect.height};
            }
            var candidates = Array.from(document.querySelectorAll('section, div'))
                .filter(visible).filter(el => !el.closest('li.sapMLIB')).map(summarize)
                .filter(item => item.text && item.text.indexOf('JOB DETAILS') >= 0
                    && item.text.indexOf('Requisition ID') >= 0 && item.text.indexOf('Recruiter') >= 0)
                .sort(function(a,b){
                    var ar = a.left > 250 ? 0 : 1, br = b.left > 250 ? 0 : 1;
                    if (ar !== br) return ar - br;
                    if (a.area !== b.area) return a.area - b.area;
                    return a.top - b.top;
                });
            var panelText = candidates.length ? candidates[0].rawText : '';
            var summaryText = panelText ? panelText.split(/JOB DETAILS/i)[0] : '';
            var parsed = parseSummaryText(summaryText);
            var summaryLines = summaryText.split(/\\n+/).map(clean).filter(Boolean);
            if (summaryLines.length) {
                var st = summaryLines[0];
                if (st && !/agency access/i.test(st)) parsed.title = st;
            }
            return {title: parsed.title, recruiter_name: parsed.recruiter_name, recruiter_email: parsed.recruiter_email};
        }""")

    def _extract_recruiter_from_sap_controls(self):
        return self.page.evaluate("""() => {
            try {
                function clean(text) { return (text || '').replace(/\\s+/g,' ').trim(); }
                function visibleDom(dom) {
                    if (!dom) return false;
                    var style = window.getComputedStyle(dom);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    var rect = dom.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                var recruiterName = '', recruiterEmail = '';
                var elements = Object.values((window.sap && sap.ui && sap.ui.getCore && sap.ui.getCore().mElements) || {});
                for (var i = 0; i < elements.length; i++) {
                    var ctrl = elements[i];
                    if (!ctrl || !ctrl.getMetadata) continue;
                    var dom = ctrl.getDomRef ? ctrl.getDomRef() : null;
                    if (!visibleDom(dom)) continue;
                    if (dom && dom.closest && dom.closest('li.sapMLIB')) continue;
                    var value = '';
                    if (!value && ctrl.getText)  value = ctrl.getText();
                    if (!value && ctrl.getTitle) value = ctrl.getTitle();
                    if (!value && ctrl.getValue) value = ctrl.getValue();
                    if (!value && dom) value = dom.innerText || dom.textContent || '';
                    value = clean(value);
                    if (!value) continue;
                    if (!recruiterName && /recruiter|client recruiter|agency contact/i.test(value) && value.length < 200) {
                        var nm = value.match(/(?:Recruiter|Client Recruiter|Agency Contact)\\s*:?\\s*(.+)$/i);
                        if (nm) recruiterName = clean(nm[1]);
                    }
                    if (!recruiterEmail) {
                        var em = value.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/i);
                        if (em) recruiterEmail = em[0];
                    }
                }
                return {recruiter_name: recruiterName, recruiter_email: recruiterEmail};
            } catch (e) {
                return {recruiter_name: '', recruiter_email: '', error: e.message};
            }
        }""")

    def _wait_for_recruiter_details(self, timeout=8):
        end    = time.time() + timeout
        latest = {"panel": {}, "sap": {}}
        while time.time() < end:
            try:    panel = self._extract_job_panel_details()
            except: panel = {}
            try:    sap = self._extract_recruiter_from_sap_controls()
            except: sap = {}
            latest = {"panel": panel, "sap": sap}
            if (panel.get("recruiter_name") or panel.get("recruiter_email")
                    or sap.get("recruiter_name") or sap.get("recruiter_email")):
                return latest
            try:
                self.page.evaluate("() => window.scrollBy(0, 250)")
            except Exception:
                pass
            time.sleep(0.7)
        return latest

    def _open_recruiter_contact_card(self, recruiter_name):
        result = self.page.evaluate("""() => {
            try {
                function visible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    var rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                var icons = Array.from(document.querySelectorAll(
                    '[data-sap-ui*="quickViewDetails"], [id*="quickViewDetails"]'
                )).filter(visible);
                for (var i = 0; i < icons.length; i++) {
                    var icon = icons[i]; var node = icon;
                    while (node) {
                        if (node.id && window.sap && sap.ui && sap.ui.getCore) {
                            var ctrl = sap.ui.getCore().byId(node.id);
                            if (ctrl) {
                                if (ctrl.firePress) { ctrl.firePress(); return {ok:true,method:'firePress',id:node.id}; }
                                if (ctrl.ontap) { ctrl.ontap({srcControl:ctrl,setMarked:function(){},preventDefault:function(){},stopPropagation:function(){},isMarked:function(){return false;},target:node}); return {ok:true,method:'ontap',id:node.id}; }
                            }
                        }
                        node = node.parentElement;
                    }
                    icon.scrollIntoView({block:'center'});
                    ['mouseenter','mouseover','mousedown','mouseup','click'].forEach(function(evt){
                        icon.dispatchEvent(new MouseEvent(evt,{bubbles:true,cancelable:true,view:window}));
                    });
                    return {ok:true,method:'mouse_events'};
                }
                if (window.sap && sap.ui && sap.ui.getCore) {
                    var elements = Object.values(sap.ui.getCore().mElements || {});
                    for (var j = 0; j < elements.length; j++) {
                        var c = elements[j];
                        if (!c || !c.getId) continue;
                        var cid = c.getId();
                        if (cid.indexOf('quickViewDetails') < 0 && cid.indexOf('quickview') < 0) continue;
                        var dom = c.getDomRef ? c.getDomRef() : null;
                        if (!visible(dom)) continue;
                        if (c.firePress) { c.firePress(); return {ok:true,method:'sap_core_firePress',id:cid}; }
                        if (dom) {
                            dom.scrollIntoView({block:'center'});
                            ['mousedown','mouseup','click'].forEach(function(evt){
                                dom.dispatchEvent(new MouseEvent(evt,{bubbles:true,cancelable:true,view:window}));
                            });
                            return {ok:true,method:'sap_core_dom_click',id:cid};
                        }
                    }
                }
                return {ok:false,reason:'quickViewDetails_not_found'};
            } catch(e) { return {ok:false,reason:e.message}; }
        }""")
        print(f"Contact card open result: {result}")
        self._screenshot("02a_before_contact_wait")

        for _ in range(6):
            popovers = self.page.query_selector_all(
                ".sapMPopover:not([style*='display: none']), .sapMQuickView:not([style*='display: none'])"
            )
            if any(p.is_visible() for p in popovers):
                self._screenshot("02a_contact_popover_opened")
                return True
            time.sleep(0.5)

        # Fallback: direct Playwright hover+click
        icons = self.page.query_selector_all("[data-sap-ui*='quickViewDetails'], [id*='quickViewDetails']")
        for icon in icons:
            try:
                icon.scroll_into_view_if_needed()
                self.page.wait_for_timeout(300)
                icon.hover()
                self.page.wait_for_timeout(150)
                icon.click()
                self.page.wait_for_timeout(1000)
                popovers = self.page.query_selector_all(".sapMPopover, .sapMQuickView")
                if any(p.is_visible() for p in popovers):
                    self._screenshot("02a_contact_popover_opened")
                    return True
            except Exception:
                continue
        return False

    def _extract_contact_from_popover(self):
        self.page.wait_for_timeout(400)
        try:
            raw = self.page.evaluate("""() => {
                var selectors = ['.sapMPopover','.sapMQuickView','.sapMPopup','[role="dialog"]'];
                for (var i = 0; i < selectors.length; i++) {
                    var els = Array.from(document.querySelectorAll(selectors[i]));
                    for (var j = 0; j < els.length; j++) {
                        var el = els[j];
                        var style = window.getComputedStyle(el);
                        var rect  = el.getBoundingClientRect();
                        if (style.display !== 'none' && rect.width > 0 && rect.height > 0)
                            return el.innerText || '';
                    }
                }
                return '';
            }""")
        except Exception as e:
            print(f"Popover text extraction error: {e}")
            return {"name": "", "email": "", "text": ""}

        if not raw:
            return {"name": "", "email": "", "text": ""}

        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        print(f"Popover lines: {lines}")
        email = ""
        name  = ""

        for i, line in enumerate(lines):
            if re.match(r"^email\s*address\s*:?\s*$", line, re.IGNORECASE):
                for j in range(i + 1, len(lines)):
                    m = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", lines[j], re.IGNORECASE)
                    if m:
                        email = m.group(0)
                        break
            m_inline = re.match(r"^email\s*address\s*:?\s*(.+)$", line, re.IGNORECASE)
            if m_inline:
                m2 = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", m_inline.group(1), re.IGNORECASE)
                if m2 and not email:
                    email = m2.group(0)
            if not email:
                m3 = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", line, re.IGNORECASE)
                if m3:
                    email = m3.group(0)

        skip = re.compile(
            r"^(contact\s*card|employee\s*details|business\s*card|email|mobile|phone|"
            r"address|recruiter|agency|[A-Z0-9._%+\-]+@[A-Z0-9.\-]+)",
            re.IGNORECASE,
        )
        for line in lines:
            if skip.match(line):
                continue
            if len(line) > 2:
                name = line
                break

        return {"name": name, "email": email, "text": raw}

    def get_job_email_details(self, req_id):
        req_id = str(req_id).strip()
        if not self.find_and_open_job(req_id):
            raise Exception(f"Requisition ID {req_id} not found in job list")

        try:    details = self._extract_job_panel_details()
        except: details = {}

        try:    recruiter_sources = self._wait_for_recruiter_details(timeout=8)
        except: recruiter_sources = {"panel": {}, "sap": {}}

        panel_fb = recruiter_sources.get("panel") or {}
        sap_fb   = recruiter_sources.get("sap") or {}

        recruiter_name = (
            (details.get("recruiter_name") or "").strip()
            or (panel_fb.get("recruiter_name") or "").strip()
            or (sap_fb.get("recruiter_name") or "").strip()
        )

        contact        = {"name": "", "email": ""}
        contact_opened = False

        for attempt in range(2):
            try:   contact_opened = self._open_recruiter_contact_card(recruiter_name) or contact_opened
            except Exception as e: print(f"[WARN] contact card attempt {attempt+1}: {e}")
            try:   contact = self._extract_contact_from_popover()
            except Exception as e:
                print(f"[WARN] popover extraction attempt {attempt+1}: {e}")
                contact = {"name": "", "email": ""}
            if contact.get("email"):
                break
            time.sleep(0.5)

        final_name = (
            (contact.get("name") or "").strip()
            or recruiter_name
            or (details.get("recruiter_name") or "").strip()
        )
        final_email = (
            (contact.get("email") or "").strip()
            or (details.get("recruiter_email") or "").strip()
            or (panel_fb.get("recruiter_email") or "").strip()
            or (sap_fb.get("recruiter_email") or "").strip()
        )
        self._screenshot("02a_recruiter_contact_details")
        return {
            "jr_number": req_id,
            "job_title": (details.get("title") or "").strip(),
            "client_recruiter": final_name,
            "email_to": final_email,
            "contact_card_opened": contact_opened,
        }

    def _activate_sap_control_from_element(self, element: ElementHandle):
        return element.evaluate("""(node) => {
            try {
                while (node) {
                    if (node.id) {
                        var ctrl = sap.ui.getCore().byId(node.id);
                        if (ctrl) {
                            if (ctrl.firePress) { ctrl.firePress(); return 'firePress:' + node.id; }
                            if (ctrl.ontap) { ctrl.ontap({srcControl:ctrl}); return 'ontap:' + node.id; }
                        }
                    }
                    node = node.parentElement;
                }
                return 'control_not_found';
            } catch (e) { return 'error:' + e.message; }
        }""")

    def _set_terms_checkbox(self):
        # Scroll dialog to bottom to reveal checkbox
        try:
            dialog = self.page.query_selector(
                "section.sapMDialogSection, div.sapMDialogScrollCont"
            )
            if dialog:
                dialog.evaluate("el => el.scrollTop = el.scrollHeight")
            self.page.wait_for_timeout(800)
        except Exception:
            pass

        result = self.page.evaluate("""() => {
            try {
                function visible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    var rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                function inDialog(el) { return !!(el && el.closest && el.closest('.sapMDialog, [role="dialog"]')); }
                function clickLike(el) {
                    if (!el) return false;
                    el.scrollIntoView({block:'center'});
                    ['mousedown','mouseup','click'].forEach(function(evt){
                        el.dispatchEvent(new MouseEvent(evt,{bubbles:true,cancelable:true,view:window}));
                    });
                    if (el.click) el.click();
                    return true;
                }
                var controls = Object.values(sap.ui.getCore().mElements || {});
                var target = null;
                for (var c of controls) {
                    if (!c.getMetadata) continue;
                    var name = c.getMetadata().getName();
                    if (name !== 'sap.m.CheckBox' && name !== 'sap.m.CheckBoxListItem') continue;
                    if (c.getVisible && c.getVisible() === false) continue;
                    var dom = c.getDomRef ? c.getDomRef() : null;
                    if (!visible(dom) || !inDialog(dom)) continue;
                    var text = ((c.getText && c.getText()) || (dom.innerText || '')).toLowerCase();
                    if (!target) target = c;
                    if (text.includes('term') || text.includes('agree') || text.includes('consent') || text.includes('condition') || text.includes('privacy')) { target = c; break; }
                }
                if (target) {
                    if (target.setSelected) target.setSelected(true);
                    if (target.fireSelect) target.fireSelect({selected:true});
                    if (target.firePress) target.firePress();
                    return {ok:true, source:'sap_control', selected: target.getSelected ? target.getSelected() : null};
                }
                var dialogCheckboxes = Array.from(document.querySelectorAll(
                    '.sapMDialog [role="checkbox"], [role="dialog"] [role="checkbox"], ' +
                    '.sapMDialog input[type="checkbox"], [role="dialog"] input[type="checkbox"], ' +
                    '.sapMDialog .sapMCb, [role="dialog"] .sapMCb'
                )).filter(visible);
                if (!dialogCheckboxes.length) return {ok:false, reason:'checkbox_not_found'};
                var domTarget = dialogCheckboxes.find(function(el){
                    var holder = el.closest('label,div,section,li');
                    var text = ((el.innerText||'') + ' ' + (holder ? holder.innerText||'' : '')).toLowerCase();
                    return text.includes('term') || text.includes('agree') || text.includes('consent') || text.includes('privacy');
                }) || dialogCheckboxes[dialogCheckboxes.length - 1];
                clickLike(domTarget);
                var nestedInput = domTarget.matches('input[type="checkbox"]') ? domTarget : (domTarget.querySelector ? domTarget.querySelector('input[type="checkbox"]') : null);
                if (nestedInput) {
                    nestedInput.checked = true;
                    nestedInput.dispatchEvent(new Event('input',{bubbles:true}));
                    nestedInput.dispatchEvent(new Event('change',{bubbles:true}));
                }
                return {ok:true, source:'dom_checkbox', checked: domTarget.getAttribute('aria-checked')==='true' || !!domTarget.checked || (domTarget.className||'').indexOf('sapMCbMarkChecked') >= 0};
            } catch (e) { return {ok:false, reason:e.message}; }
        }""")
        print(f"Checkbox result: {result}")

        if isinstance(result, dict) and result.get("checked") is True:
            print("Checkbox accepted via SAP control")
            return

        # Playwright fallback: find and click directly
        checkbox = self.page.wait_for_selector(
            "xpath=(//div[contains(@class,'sapMDialog')]//*[@role='checkbox'] | "
            "//div[@role='dialog']//*[@role='checkbox'] | "
            "//div[contains(@class,'sapMDialog')]//input[@type='checkbox'] | "
            "//div[@role='dialog']//input[@type='checkbox'] | "
            "//div[contains(@class,'sapMDialog')]//*[contains(@class,'sapMCb')] | "
            "//div[@role='dialog']//*[contains(@class,'sapMCb')])[last()]",
            timeout=10000,
        )
        checkbox.scroll_into_view_if_needed()
        self.page.wait_for_timeout(500)

        def is_checked():
            try:
                aria = checkbox.get_attribute("aria-checked")
                cls  = checkbox.get_attribute("class") or ""
                html = checkbox.evaluate("el => el.outerHTML")
                return aria == "true" or "sapMCbMarkChecked" in cls or "sapMCbMarkChecked" in html
            except Exception:
                return False

        if not is_checked():
            inner = self.page.query_selector_all(
                "xpath=//div[contains(@class,'sapMDialog')]//*[contains(@class,'sapMCbBg') or contains(@class,'sapMCbMark')]"
                " | //div[@role='dialog']//*[contains(@class,'sapMCbBg') or contains(@class,'sapMCbMark')]"
            )
            if inner:
                inner[-1].evaluate("""el => {
                    ['mousedown','mouseup','click'].forEach(function(evt){
                        el.dispatchEvent(new MouseEvent(evt,{bubbles:true,cancelable:true,view:window}));
                    });
                    if (el.click) el.click();
                }""")
                self.page.wait_for_timeout(400)

        if not is_checked():
            try:
                checkbox.hover()
                self.page.wait_for_timeout(200)
                checkbox.click()
            except Exception:
                checkbox.evaluate("el => el.click()")
            self.page.wait_for_timeout(500)

        if not is_checked():
            self._screenshot("06_terms_checkbox_error")
            raise Exception("Terms checkbox remained unchecked after all click attempts")

        print(f"Checkbox state: aria-checked={checkbox.get_attribute('aria-checked')}")

    def _press_dialog_button(self, text: str):
        # Strategy 1: SAP API firePress
        result = self.page.evaluate("""(wanted) => {
            try {
                function visible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') return false;
                    var rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                function activeDialog() {
                    var dialogs = Array.from(document.querySelectorAll('.sapMDialog, [role="dialog"]')).filter(visible);
                    if (!dialogs.length) return null;
                    dialogs.sort(function(a,b){
                        var zA = parseInt(window.getComputedStyle(a).zIndex||'0',10);
                        var zB = parseInt(window.getComputedStyle(b).zIndex||'0',10);
                        return zA !== zB ? zA - zB : 0;
                    });
                    return dialogs[dialogs.length - 1];
                }
                var dialog = activeDialog();
                if (!dialog) return 'dialog_not_found';
                var allControls = Object.values((((window.sap||{}).ui||{}).getCore||function(){return{};})().mElements||{});
                for (var c of allControls) {
                    if (!c.getMetadata || c.getMetadata().getName() !== 'sap.m.Button') continue;
                    if (!c.getText || c.getText().trim() !== wanted) continue;
                    if (c.getVisible && c.getVisible() === false) continue;
                    if (c.getEnabled && c.getEnabled() === false) continue;
                    var dom = c.getDomRef ? c.getDomRef() : null;
                    if (!visible(dom)) continue;
                    if (!(dialog === dom || dialog.contains(dom))) continue;
                    c.firePress();
                    return 'firePress:' + (c.getId ? c.getId() : wanted);
                }
                return 'not_found';
            } catch (e) { return 'error:' + e.message; }
        }""", text)
        print(f"Dialog button '{text}' result: {result}")
        if "firePress:" in str(result):
            return result

        # Strategy 2: DOM button click
        dom_result = self.page.evaluate("""(wanted) => {
            try {
                function visible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') return false;
                    var rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                function labelOf(el) {
                    return (el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('value') || el.innerText || el.textContent || '').replace(/\\s+/g,' ').trim();
                }
                function clickLike(el) {
                    el.scrollIntoView({block:'center'});
                    ['mousedown','mouseup','click'].forEach(function(evt){
                        if (typeof el.dispatchEvent === 'function') el.dispatchEvent(new MouseEvent(evt,{bubbles:true,cancelable:true,view:window}));
                    });
                    if (typeof el.click === 'function') el.click();
                    return true;
                }
                var dialog = document.querySelector('.sapMDialog, [role="dialog"]');
                if (!dialog) return {ok:false, reason:'dialog_not_found'};
                var nodes = Array.from(dialog.querySelectorAll('button, input[type="button"], [role="button"], .sapMBtn')).filter(visible);
                var target = nodes.find(el => labelOf(el).toLowerCase() === wanted.toLowerCase())
                          || nodes.find(el => labelOf(el).toLowerCase().includes(wanted.toLowerCase()));
                if (!target) return {ok:false, reason:'not_found'};
                clickLike(target);
                return {ok:true, label: labelOf(target)};
            } catch (e) { return {ok:false, reason:e.message}; }
        }""", text)
        print(f"Dialog button '{text}' DOM result: {dom_result}")
        if isinstance(dom_result, dict) and dom_result.get("ok"):
            return dom_result

        # Strategy 3: Playwright locator
        try:
            btn = self.page.wait_for_selector(
                f"xpath=//div[contains(@class,'sapMDialog')]//button[normalize-space()='{text}'] | "
                f"//div[contains(@class,'sapMDialog')]//bdi[normalize-space()='{text}']/ancestor::button[1] | "
                f"//div[@role='dialog']//button[normalize-space()='{text}']",
                timeout=5000,
            )
            btn.scroll_into_view_if_needed()
            self.page.wait_for_timeout(300)
            btn.hover()
            btn.click()
            return "playwright_click"
        except Exception as e:
            raise Exception(f"Unable to locate dialog button '{text}': {e}")

    # =========================
    # FIND & OPEN JOB
    # =========================
    def find_and_open_job(self, req_id: str) -> bool:
        req_id         = str(req_id).strip()
        normalized_req = re.sub(r"\s+", "", req_id).lower()
        print(f"Searching Requisition ID: {req_id}")

        self.page.wait_for_selector("section.sapMPageEnableScrolling", timeout=20000)

        for i in range(50):
            jobs = self.page.query_selector_all("li.sapMLIB")
            print(f"Iteration {i + 1} | Jobs visible: {len(jobs)}")

            target_idx = None
            for idx, job in enumerate(jobs):
                try:
                    job_text = re.sub(r"\s+", "", job.inner_text() or "").lower()
                    if normalized_req in job_text:
                        target_idx = idx
                        print(f"Found JR {req_id} at index {idx}")
                        break
                except Exception:
                    continue

            if target_idx is not None:
                target = jobs[target_idx]
                target.scroll_into_view_if_needed()
                self.page.wait_for_timeout(500)

                activation = self._activate_sap_control_from_element(target)
                print(f"SAP control activation: {activation}")

                try:
                    target.hover()
                    target.click()
                except Exception:
                    pass

                self.page.evaluate("""(idx) => {
                    var items = document.querySelectorAll("li.sapMLIB");
                    var el = items[idx];
                    if (el) {
                        el.scrollIntoView({block:'center'});
                        ['mousedown','mouseup','click'].forEach(function(evt){
                            el.dispatchEvent(new MouseEvent(evt,{bubbles:true,cancelable:true,view:window}));
                        });
                        el.click();
                    }
                }""", target_idx)

                matched, snippet = self._wait_for_details_panel(req_id, timeout=15)
                if matched:
                    print(f"Opened Requisition ID: {req_id} | {snippet}")
                    self._screenshot("02_job_opened_and_verified")
                    return True

                print(f"Details panel did not update for JR {req_id}. Last: {snippet}")
                self._screenshot("02_job_open_failed")
                return False

            self.page.evaluate("""() => {
                var c = document.querySelector('section.sapMPageEnableScrolling');
                if (c) c.scrollBy(0, 300);
            }""")
            self.page.wait_for_timeout(1500)

        print(f"Requisition ID {req_id} not found after scrolling")
        self._screenshot("02_job_not_found")
        return False

    # =========================
    # OPEN ADD CANDIDATE FORM
    # =========================
    def _open_add_candidate_form(self, jr_number: str):
        matched, snippet = self._wait_for_details_panel(jr_number, timeout=10)
        if not matched:
            raise Exception(f"Right panel did not show JR {jr_number}. Last: {snippet}")
        self.page.wait_for_timeout(2000)

        menu_btn = None
        for selector in [
            "span[aria-label='Actions']",
            "button[id*='overflowButton']",
            "button[id*='action']",
            "button.sapMBtn[id*='action']",
        ]:
            el = self.page.query_selector(selector)
            if el:
                menu_btn = el
                print(f"Actions button found via: {selector}")
                break

        if not menu_btn:
            panel_btns = self.page.query_selector_all(".sapMFlexBox button, .sapMPage button")
            if panel_btns:
                menu_btn = panel_btns[-1]
                print("Using last panel button")

        if not menu_btn:
            self._screenshot("03_actions_button_error")
            raise Exception("Cannot find Actions button")

        menu_btn.evaluate("el => el.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,view:window}))")
        self.page.wait_for_timeout(2000)
        self._screenshot("03_actions_menu_opened")

        try:
            submit_el = self.page.wait_for_selector("text=Submit New Candidate", timeout=10000)
            ctrl_id   = submit_el.get_attribute("id")
            if ctrl_id:
                result = self.page.evaluate(f"""() => {{
                    try {{
                        var ctrl = sap.ui.getCore().byId('{ctrl_id}');
                        if (ctrl && ctrl.firePress) {{ ctrl.firePress(); return 'firePress:ok'; }}
                        if (ctrl && ctrl.ontap) {{ ctrl.ontap({{srcControl:ctrl}}); return 'ontap:ok'; }}
                        return 'ctrl_not_found';
                    }} catch(e) {{ return 'error:' + e.message; }}
                }}""")
                print(f"Submit New Candidate result: {result}")
            submit_el.click()
            self.page.wait_for_timeout(3000)
            self._screenshot("04_submit_new_candidate_clicked")
        except Exception as e:
            self._screenshot("04_submit_new_candidate_error")
            raise Exception(f"Submit New Candidate click failed: {e}")

        try:
            self.page.wait_for_selector(
                "xpath=//input[@placeholder='Please enter first name.']", timeout=30000
            )
        except Exception as e:
            self._screenshot("04_form_not_opened")
            raise Exception(f"Add Candidate form did not open: {e}")

        print("Add Candidate form opened")

    # =========================
    # FILL & SUBMIT FORM
    # =========================
    def upload_candidate(self, data: dict):
        jr = str(data["jr_number"]).strip()

        if not self.find_and_open_job(jr):
            raise Exception(f"Requisition ID {jr} not found in job list")

        try:
            self._open_add_candidate_form(jr)
        except Exception as e:
            raise Exception(f"Failed to open Add Candidate form: {e}")

        try:
            self._fill("//input[@placeholder='Please enter first name.']",  data["first_name"])
            self._fill("//input[@placeholder='Please enter last name.']",   data["last_name"])
            self._fill("//input[@placeholder='Please enter email.']",       data["email"])
            self._fill("//input[@placeholder='Re-enter the email address']", data["email"])
            self._fill("//input[@placeholder='Please enter phone number']", data["phone"])
        except Exception as e:
            raise Exception(f"Failed to fill text fields: {e}")

        try:
            r1 = self._sap_select("phoneCodeDlgFld", data.get("country_code", "+91"), by="key")
            print(f"Country code: {r1}")
            r2 = self._sap_select("countryDlgFld", data.get("country", "India"), by="text")
            print(f"Country: {r2}")
        except Exception as e:
            raise Exception(f"Failed to set dropdowns: {e}")

        try:
            self.page.locator("input[type='file']").set_input_files(data["resume_path"])
            self.page.wait_for_timeout(1000)
            print("Resume uploaded")
            self._screenshot("05_form_filled_resume_uploaded")
        except Exception as e:
            raise Exception(f"Failed to upload resume: {e}")

        try:
            self._set_terms_checkbox()
            self._screenshot("06_terms_checked")
        except Exception as e:
            raise Exception(f"Failed to check terms checkbox: {e}")

        self.page.wait_for_timeout(5000)

        if data.get("submit", True):
            self._screenshot("07_before_add_candidate")
            self._press_dialog_button("Add Candidate")

            deadline  = time.time() + 15
            submitted = False
            while time.time() < deadline:
                try:
                    dialogs       = self.page.query_selector_all(".sapMDialog")
                    still_visible = any(d.is_visible() for d in dialogs)
                    if not still_visible:
                        submitted = True
                        break
                except Exception:
                    submitted = True
                    break
                if self._is_existing_candidate_dialog():
                    self._screenshot("08_existing_candidate_dialog")
                    sap_msg = self._extract_screen_error() or ""
                    try:
                        self._press_dialog_button("Cancel")
                        self.page.wait_for_timeout(1000)
                    except Exception:
                        pass
                    raise Exception(f"Candidate already exists in SAP|{sap_msg}")
                time.sleep(0.5)

            if submitted:
                print(f"Candidate submitted for JR {jr}")
                self._screenshot("08_after_add_candidate")
            else:
                self._screenshot("08_dialog_not_closed")
                raise Exception("Dialog did not close after submission — verify manually")
        else:
            self._screenshot("07_before_cancel")
            self._press_dialog_button("Cancel")
            try:
                self.page.wait_for_selector(
                    "xpath=//div[contains(@class,'sapMDialog')]",
                    state="hidden", timeout=15000,
                )
            except Exception:
                raise Exception("Dialog did not close after cancel — verify manually")
            print(f"Cancelled form for JR {jr} (dry run)")
            self._screenshot("08_after_cancel")
