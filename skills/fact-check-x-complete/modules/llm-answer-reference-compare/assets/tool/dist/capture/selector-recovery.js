const INPUT_QUERY = "textarea, input, [contenteditable='true'], [role='textbox']";
const ANSWER_QUERY = [
    "[data-message-author-role='assistant']",
    "[role='article']",
    "article",
    "[class*='assistant']",
    "[class*='answer']",
    "[id*='answer']",
    "[class*='message']",
    "main"
].join(", ");

function normalize(value) {
    return String(value || "").replace(/\s+/g, "").trim();
}

function looksLikeProgress(value) {
    const text = normalize(value).replace(/[。！!，,…\.]+$/g, "");
    return !text
        || /^(?:我(?:来|正在)?|正在)?(?:为你|为您)?(?:查证|核实|检索|搜索|分析|思考|生成|整理)(?:中|资料|相关资料|内容|信息|答案)?(?:请稍候)?$/.test(text)
        || /^\[[^\]]{1,20}\]$/.test(text);
}

export async function proposeSelectorRecovery(page, operation, context = {}) {
    if (!page || page.isClosed?.()) {
        return null;
    }
    const query = operation === "input" ? INPUT_QUERY : ANSWER_QUERY;
    const root = page.locator(query);
    if (!root || typeof root.evaluateAll !== "function") {
        return null;
    }
    const candidates = await root.evaluateAll((nodes, payload) => {
        const visible = (element) => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== "none"
                && style.visibility !== "hidden"
                && Number(style.opacity || 1) > 0
                && rect.width > 1
                && rect.height > 1;
        };
        const cssEscape = (value) => window.CSS?.escape
            ? window.CSS.escape(value)
            : String(value).replace(/[^a-zA-Z0-9_-]/g, (char) => `\\${char}`);
        const pathFor = (element) => {
            if (element.id) {
                return `#${cssEscape(element.id)}`;
            }
            for (const attribute of ["data-testid", "data-test-id", "aria-label", "name"]) {
                const value = element.getAttribute(attribute);
                if (value) {
                    const escaped = String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
                    const selector = `${element.tagName.toLowerCase()}[${attribute}="${escaped}"]`;
                    if (document.querySelectorAll(selector).length === 1) {
                        return selector;
                    }
                }
            }
            const parts = [];
            let current = element;
            while (current && current !== document.body && parts.length < 6) {
                const tag = current.tagName.toLowerCase();
                const siblings = current.parentElement
                    ? Array.from(current.parentElement.children).filter((child) => child.tagName === current.tagName)
                    : [];
                const suffix = siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(current) + 1})` : "";
                parts.unshift(`${tag}${suffix}`);
                current = current.parentElement;
            }
            return `body > ${parts.join(" > ")}`;
        };
        return nodes
            .filter((node) => node instanceof HTMLElement && visible(node))
            .map((node) => {
                const text = String(
                    node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement
                        ? node.value || node.placeholder || node.getAttribute("aria-label") || ""
                        : node.innerText || node.textContent || node.getAttribute("aria-label") || ""
                ).trim();
                const editable = !node.hasAttribute("disabled") && !node.getAttribute("aria-disabled")
                    && (node instanceof HTMLInputElement
                        || node instanceof HTMLTextAreaElement
                        || node.isContentEditable
                        || node.getAttribute("role") === "textbox");
                const rect = node.getBoundingClientRect();
                return {
                    selector: pathFor(node),
                    tag: node.tagName.toLowerCase(),
                    role: node.getAttribute("role") || "",
                    name: node.getAttribute("aria-label") || node.getAttribute("placeholder") || "",
                    text,
                    editable,
                    area: Math.round(rect.width * rect.height)
                };
            });
    }, { operation, question: String(context.question || "") }).catch(() => []);

    const question = normalize(context.question);
    const previousAnswer = normalize(context.previousAnswer);
    const scored = candidates.map((candidate) => {
        const text = normalize(candidate.text);
        let score = 0;
        if (operation === "input") {
            score += candidate.editable ? 100 : -100;
            score += candidate.role === "textbox" ? 12 : 0;
            score += ["textarea", "input"].includes(candidate.tag) ? 8 : 0;
            score += /问题|提问|发送|输入|ask|message/i.test(candidate.name) ? 10 : 0;
        }
        else {
            score += text.length >= 30 ? 45 : -40;
            score += candidate.role === "article" || candidate.tag === "article" ? 16 : 0;
            score += /assistant|answer|message/i.test(candidate.selector) ? 12 : 0;
            score += candidate.tag === "main" ? -50 : 0;
            score += question && text === question ? -100 : 0;
            score += question && text.includes(question) && text.length < question.length + 30 ? -80 : 0;
            score += previousAnswer && text === previousAnswer ? -60 : 0;
            score += looksLikeProgress(text) ? -100 : 0;
            score += Math.min(20, Math.floor(text.length / 100));
        }
        return { ...candidate, score, textLength: text.length };
    }).sort((left, right) => right.score - left.score || right.textLength - left.textLength);
    const best = scored[0];
    const threshold = operation === "input" ? 100 : 45;
    if (!best || best.score < threshold) {
        return null;
    }
    if (scored[1] && scored[1].score === best.score && scored[1].textLength === best.textLength) {
        return null;
    }
    return {
        schemaVersion: "fact-check-x/selector-recovery-proposal@1",
        operation,
        selector: best.selector,
        observed: {
            tag: best.tag,
            role: best.role,
            name: best.name,
            textLength: best.textLength,
            score: best.score
        },
        validation: operation === "input"
            ? ["unique", "visible", "editable"]
            : ["unique", "visible", "not_question_echo", "not_progress", "minimum_length"],
        source: "playwright_accessibility_dom"
    };
}

export async function validateSelectorRecovery(page, proposal, context = {}) {
    if (!proposal || proposal.schemaVersion !== "fact-check-x/selector-recovery-proposal@1") {
        return null;
    }
    const locator = page.locator(proposal.selector);
    if ((await locator.count().catch(() => 0)) !== 1 || !(await locator.isVisible().catch(() => false))) {
        return null;
    }
    if (proposal.operation === "input") {
        const editable = await locator.evaluate((node) => !node.hasAttribute("disabled")
            && !node.getAttribute("aria-disabled")
            && (node instanceof HTMLInputElement
                || node instanceof HTMLTextAreaElement
                || node.isContentEditable
                || node.getAttribute("role") === "textbox")).catch(() => false);
        return editable ? locator : null;
    }
    const text = String(await locator.innerText().catch(() => "")).trim();
    const normalized = normalize(text);
    if (normalized.length < 30
        || looksLikeProgress(normalized)
        || (normalize(context.question) && normalized === normalize(context.question))
        || (normalize(context.previousAnswer) && normalized === normalize(context.previousAnswer))) {
        return null;
    }
    return locator;
}

export async function recoverSelector(page, operation, context = {}) {
    const proposal = await proposeSelectorRecovery(page, operation, context);
    if (!proposal) {
        return null;
    }
    const locator = await validateSelectorRecovery(page, proposal, context);
    return locator ? { locator, proposal } : null;
}

export function selectorRecoveryContract(recoveries = []) {
    return {
        schemaVersion: "fact-check-x/selector-recovery@1",
        policy: "model_or_host_may_propose_program_must_validate",
        primaryDiagnostic: "host_browser_or_computer_use",
        optionalDiagnostic: "agent-browser",
        agentBrowserRequired: false,
        maySubmitQuestion: false,
        proposals: Array.isArray(recoveries) ? recoveries : []
    };
}
