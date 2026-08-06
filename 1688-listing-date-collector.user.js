// ==UserScript==
// @name         1688 搜索结果商品数据分页采集
// @namespace    https://s.1688.com/
// @version      0.3.0
// @description  分页采集商品链接、图片、上架日期、年销量并计算件单量与每日笔数
// @author       Zoo
// @match        https://s.1688.com/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_xmlhttpRequest
// @connect      *
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    const STORAGE_KEY = 'zoo_1688_product_collector_v3';
    const CONFIG_KEY = 'zoo_1688_product_collector_api_config_v1';
    const PANEL_ID = 'zoo-1688-collector-panel';
    const PAGE_SELECTOR = '.fui-current.fui-page-item, .fui-current';
    const TOTAL_SELECTOR = '.pagination-container .fui-paging-total .fui-paging-num';
    const NEXT_SELECTOR = '.pagination-container .fui-arrow.fui-next';

    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    function defaultState() {
        return {
            active: false,
            startPage: null,
            endPage: null,
            groupId: null,
            groupName: '',
            currentStatus: '等待开始',
            processedPages: [],
            uploadedPages: [],
            records: [],
            startedAt: null
        };
    }

    function loadApiConfig() {
        return {
            baseUrl: '',
            apiToken: '',
            selectedGroupId: '',
            ...GM_getValue(CONFIG_KEY, {})
        };
    }

    function saveApiConfig(config) {
        GM_setValue(CONFIG_KEY, config);
    }

    function normalizeBaseUrl(value) {
        return String(value || '').trim().replace(/\/+$/, '');
    }

    function apiRequest(method, path, body = null) {
        const config = loadApiConfig();
        const baseUrl = normalizeBaseUrl(config.baseUrl);
        if (!baseUrl || !config.apiToken) {
            return Promise.reject(new Error('请先配置后台地址和 API Token'));
        }
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method,
                url: `${baseUrl}${path}`,
                headers: {
                    Authorization: `Bearer ${config.apiToken}`,
                    'Content-Type': 'application/json'
                },
                data: body === null ? undefined : JSON.stringify(body),
                timeout: 30000,
                onload(response) {
                    let parsed = null;
                    try {
                        parsed = response.responseText ? JSON.parse(response.responseText) : null;
                    } catch (_error) {
                        // Keep the response text for a useful error below.
                    }
                    if (response.status >= 200 && response.status < 300) {
                        resolve(parsed);
                    } else {
                        reject(new Error(parsed?.detail || `后台请求失败：HTTP ${response.status}`));
                    }
                },
                ontimeout() {
                    reject(new Error('后台请求超时'));
                },
                onerror() {
                    reject(new Error('无法连接后台，请检查地址、反向代理和网络'));
                }
            });
        });
    }

    async function loadBackendGroups() {
        const groups = await apiRequest('GET', '/api/ingest/groups');
        const select = document.querySelector(`#${PANEL_ID} #zoo-group`);
        if (!select) {
            return groups;
        }
        const config = loadApiConfig();
        select.innerHTML = '<option value="">请选择已有分组</option>';
        for (const group of groups) {
            const option = document.createElement('option');
            option.value = String(group.id);
            option.textContent = group.name;
            select.appendChild(option);
        }
        if (groups.some((group) => String(group.id) === String(config.selectedGroupId))) {
            select.value = String(config.selectedGroupId);
        }
        return groups;
    }

    async function uploadPageWithRetry(page, records, state) {
        const maxAttempts = 5;
        const payload = {
            idempotency_key: `1688-${state.startedAt}-${state.groupId}-page-${page}`,
            group_id: state.groupId,
            source_page: page,
            products: records.map((record) => ({
                offer_id: record.offerId,
                product_url: record.productUrl,
                image_url: record.imageUrl || null,
                listing_date: record.listingDate,
                listed_days: record.listedDays,
                total_orders: record.totalOrders,
                total_pieces: record.totalPieces
            }))
        };

        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            try {
                state.currentStatus = `第 ${page} 页采集 ${records.length} 条，正在上传（${attempt}/${maxAttempts}）`;
                saveState(state);
                return await apiRequest('POST', '/api/ingest/batches', payload);
            } catch (error) {
                if (attempt === maxAttempts) {
                    throw new Error(`第 ${page} 页上传失败：${error.message}`);
                }
                const delayMs = Math.min(30000, 2000 * (2 ** (attempt - 1)));
                state.currentStatus = `第 ${page} 页上传失败，${delayMs / 1000} 秒后重试：${error.message}`;
                saveState(state);
                await sleep(delayMs);
            }
        }
        return null;
    }

    function loadState() {
        const saved = GM_getValue(STORAGE_KEY, null);
        return saved && typeof saved === 'object'
            ? { ...defaultState(), ...saved }
            : defaultState();
    }

    function saveState(state) {
        GM_setValue(STORAGE_KEY, state);
        updatePanel(state);
    }

    function clearState() {
        GM_deleteValue(STORAGE_KEY);
    }

    function normalizeText(value) {
        return String(value || '').replace(/\s+/g, '').replace(/：/g, ':').trim();
    }

    function getCurrentPage() {
        const element = document.querySelector(PAGE_SELECTOR);
        const page = Number.parseInt(element?.textContent?.trim() || '', 10);
        return Number.isInteger(page) && page > 0 ? page : null;
    }

    function getTotalPages() {
        const element = document.querySelector(TOTAL_SELECTOR);
        const total = Number.parseInt(element?.textContent?.trim() || '', 10);
        return Number.isInteger(total) && total > 0 ? total : null;
    }

    async function waitForPagination(timeoutMs = 30000) {
        const startedAt = Date.now();
        while (Date.now() - startedAt < timeoutMs) {
            const currentPage = getCurrentPage();
            const totalPages = getTotalPages();
            if (currentPage && totalPages) {
                return { currentPage, totalPages };
            }
            await sleep(500);
        }
        throw new Error('等待分页区域超时，请确认页面已正常加载');
    }

    function parseListingValue(rawValue) {
        const text = String(rawValue || '').trim();
        const match = text.match(/(\d{4}-\d{2}-\d{2})\s*[（(]\s*(\d+)\s*天\s*[）)]/);
        if (!match) {
            return null;
        }
        return {
            listingDate: match[1],
            listedDays: Number.parseInt(match[2], 10),
            rawValue: text
        };
    }

    function parseChineseNumber(numberText, unit) {
        const value = Number.parseFloat(String(numberText || '').replace(/,/g, ''));
        if (!Number.isFinite(value)) {
            return null;
        }
        const multiplier = unit === '亿' ? 100000000 : unit === '万' ? 10000 : 1;
        return Math.round(value * multiplier);
    }

    function parseAnnualSales(rawValue) {
        const text = String(rawValue || '').trim();
        const match = text.match(/([\d,.]+)\s*([万亿]?)\s*件\s*[（(]\s*([\d,.]+)\s*([万亿]?)\s*笔\s*[）)]/);
        if (!match) {
            return null;
        }
        return {
            totalPieces: parseChineseNumber(match[1], match[2]),
            totalOrders: parseChineseNumber(match[3], match[4]),
            rawValue: text
        };
    }

    function roundMetric(value, digits = 4) {
        if (!Number.isFinite(value)) {
            return '';
        }
        return Number(value.toFixed(digits));
    }

    function findFieldValue(card, fieldName) {
        const expectedLabel = normalizeText(`${fieldName}:`);
        for (const label of card.querySelectorAll('span')) {
            if (normalizeText(label.textContent) !== expectedLabel) {
                continue;
            }
            const valueElement = label.nextElementSibling;
            return valueElement
                ? String(valueElement.getAttribute('title') || valueElement.textContent || '').trim()
                : '';
        }
        return '';
    }

    function getOfferId(card, productUrl) {
        try {
            const offerId = new URL(productUrl, location.href).searchParams.get('offerId');
            if (offerId) {
                return offerId;
            }
        } catch (_error) {
            // Fall through to tracking attributes when the URL is malformed.
        }
        const trackerText = `${card.getAttribute('data-renderkey') || ''} ${card.getAttribute('data-aplus-report') || ''}`;
        return trackerText.match(/(?:offerId=|object_id@|item_id@)(\d+)/)?.[1] || '';
    }

    function findProductRecordsInDom(page) {
        const records = [];
        const cards = document.querySelectorAll('a.search-offer-wrapper, .search-offer-wrapper');

        for (const card of cards) {
            const listingRawValue = findFieldValue(card, '上架日期');
            const listing = parseListingValue(listingRawValue);
            if (!listing) {
                continue;
            }

            const annualSalesRawValue = findFieldValue(card, '年销量');
            const annualSales = parseAnnualSales(annualSalesRawValue);
            const linkElement = card.matches('a[href]') ? card : card.querySelector('a[href*="offerId="]');
            const imageElement = card.querySelector('img.main-img, .offer-img-wrapper img');
            const productUrl = linkElement?.href || '';
            const offerId = getOfferId(card, productUrl);
            if (!annualSales || !offerId || !productUrl) {
                // Do not create a partially loaded record: one malformed item would
                // make the backend reject the complete page batch.
                continue;
            }
            const imageUrl = imageElement?.currentSrc
                || imageElement?.src
                || imageElement?.getAttribute('data-src')
                || '';
            const totalPieces = annualSales.totalPieces;
            const totalOrders = annualSales.totalOrders;
            const piecesPerOrder = totalOrders > 0 ? totalPieces / totalOrders : null;

            // 按当前业务规则，将平均每笔件数大于或等于 300 的商品视为异常并排除。
            if (piecesPerOrder !== null && piecesPerOrder >= 300) {
                continue;
            }

            const calculationDays = listing.listedDays > 360 ? 220 : listing.listedDays;
            const averagePiecesPerOrder = piecesPerOrder !== null
                ? roundMetric(piecesPerOrder)
                : '';
            const dailyOrders = totalOrders !== '' && calculationDays > 0
                ? roundMetric(totalOrders / calculationDays)
                : '';

            records.push({
                sourcePage: page,
                offerId,
                productUrl,
                imageUrl,
                listingDate: listing.listingDate,
                listedDays: listing.listedDays,
                annualSalesRawValue,
                totalPieces,
                totalOrders,
                averagePiecesPerOrder,
                calculationDays,
                dailyOrders
            });
        }

        return records;
    }

    function mergePageRecords(recordMap, records) {
        records.forEach((record, index) => {
            const key = record.offerId
                || record.productUrl
                || `${record.sourcePage}|${record.listingDate}|${record.annualSalesRawValue}|${index}`;
            recordMap.set(key, record);
        });
    }

    async function collectCurrentPage(page) {
        const recordMap = new Map();
        let stableRounds = 0;
        let previousCount = -1;
        let lastHeight = -1;
        const startedAt = Date.now();
        const timeoutMs = 45000;

        window.scrollTo({ top: 0, behavior: 'auto' });
        await sleep(800);

        while (Date.now() - startedAt < timeoutMs) {
            mergePageRecords(recordMap, findProductRecordsInDom(page));

            const currentHeight = Math.max(
                document.body.scrollHeight,
                document.documentElement.scrollHeight
            );
            const nextTop = Math.min(
                window.scrollY + Math.max(window.innerHeight * 0.85, 500),
                currentHeight
            );

            if (recordMap.size === previousCount && currentHeight === lastHeight) {
                stableRounds += 1;
            } else {
                stableRounds = 0;
            }

            previousCount = recordMap.size;
            lastHeight = currentHeight;

            const reachedBottom = window.scrollY + window.innerHeight >= currentHeight - 80;
            if (reachedBottom && stableRounds >= 3) {
                break;
            }

            window.scrollTo({ top: nextTop, behavior: 'auto' });
            // 等待商品卡片中的年销量、上架日期和图片完成延迟加载。
            await sleep(1000);
        }

        await sleep(1000);
        mergePageRecords(recordMap, findProductRecordsInDom(page));
        return Array.from(recordMap.values());
    }

    function isNextButtonDisabled(button) {
        if (!button) {
            return true;
        }
        const classText = button.className || '';
        return button.getAttribute('aria-disabled') === 'true'
            || button.hasAttribute('disabled')
            || /disabled|forbid/i.test(classText);
    }

    async function goToNextPage(currentPage) {
        const maxAttempts = 3;
        const timeoutMs = 15000;

        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'auto' });
            await sleep(500);

            const button = document.querySelector(NEXT_SELECTOR);
            if (isNextButtonDisabled(button)) {
                throw new Error(`第 ${currentPage} 页找不到可用的“下一页”按钮`);
            }

            button.click();

            const startedAt = Date.now();
            while (Date.now() - startedAt < timeoutMs) {
                await sleep(500);
                const nextPage = getCurrentPage();
                if (nextPage && nextPage !== currentPage) {
                    const delayMs = Math.floor(3000 + Math.random() * 2001);
                    const state = loadState();
                    state.currentStatus = `已翻到第 ${nextPage} 页，等待 ${(delayMs / 1000).toFixed(1)} 秒后开始采集`;
                    saveState(state);
                    await sleep(delayMs);
                    return nextPage;
                }
            }

            if (attempt < maxAttempts) {
                const state = loadState();
                state.currentStatus = `第 ${currentPage} 页翻页未响应，正在进行第 ${attempt + 1}/${maxAttempts} 次尝试`;
                saveState(state);
                await sleep(1500);
            }
        }

        throw new Error(`点击下一页 ${maxAttempts} 次后，页码仍未发生变化`);
    }

    function csvEscape(value) {
        const text = String(value ?? '');
        return `"${text.replace(/"/g, '""')}"`;
    }

    function downloadCsv(records, state, partial = false) {
        const rows = [
            ['分组', '上传状态', '来源页', '商品ID', '商品链接', '图片链接', '上架日期', '上架天数', '年销量原文', '总件数', '总笔数', '平均每笔件数', '每日笔数计算天数', '每日笔数'],
            ...records.map((record) => [
                state.groupName || '',
                state.uploadedPages.includes(record.sourcePage) ? '已上传' : '未上传',
                record.sourcePage,
                record.offerId,
                record.productUrl,
                record.imageUrl,
                record.listingDate,
                record.listedDays,
                record.annualSalesRawValue,
                record.totalPieces,
                record.totalOrders,
                record.averagePiecesPerOrder,
                record.calculationDays,
                record.dailyOrders
            ])
        ];
        const csv = `\uFEFF${rows.map((row) => row.map(csvEscape).join(',')).join('\r\n')}`;
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        const range = `${state.startPage || 'unknown'}-${state.endPage || 'unknown'}`;
        anchor.href = url;
        anchor.download = `1688商品数据_${range}页_${partial ? '部分_' : ''}${timestamp}.csv`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
    }

    function createPanel() {
        if (document.getElementById(PANEL_ID)) {
            return;
        }

        const panel = document.createElement('section');
        panel.id = PANEL_ID;
        const apiConfig = loadApiConfig();
        panel.innerHTML = `
            <div class="zoo-title">1688 商品数据采集</div>
            <label class="zoo-field">
                <span>后台地址</span>
                <input id="zoo-api-url" type="url" placeholder="https://products.example.com">
            </label>
            <label class="zoo-field">
                <span>API Token</span>
                <input id="zoo-api-token" type="password" placeholder="后台 INGEST_API_TOKEN">
            </label>
            <label class="zoo-field">
                <span>后台分组</span>
                <select id="zoo-group"><option value="">请加载分组</option></select>
            </label>
            <label class="zoo-row">
                <span>结束页（包含）</span>
                <input id="zoo-end-page" type="number" min="1" step="1" placeholder="例如 10">
            </label>
            <div class="zoo-actions">
                <button id="zoo-load-groups" type="button">保存配置并加载分组</button>
                <button id="zoo-start" type="button">开始采集</button>
                <button id="zoo-stop" type="button">停止</button>
                <button id="zoo-export" type="button">导出已有数据</button>
            </div>
            <div id="zoo-status">等待开始</div>
            <div id="zoo-summary"></div>
        `;

        const style = document.createElement('style');
        style.textContent = `
            #${PANEL_ID} {
                position: fixed; right: 18px; top: 90px; z-index: 2147483647;
                width: 330px; box-sizing: border-box; padding: 14px;
                border: 1px solid #ff6400; border-radius: 10px;
                background: rgba(255,255,255,.97); color: #222;
                box-shadow: 0 6px 24px rgba(0,0,0,.18);
                font: 13px/1.5 Arial, "Microsoft YaHei", sans-serif;
            }
            #${PANEL_ID} .zoo-title { font-size: 16px; font-weight: 700; margin-bottom: 10px; color: #ff4000; }
            #${PANEL_ID} .zoo-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
            #${PANEL_ID} .zoo-field { display: grid; grid-template-columns: 82px 1fr; align-items: center; gap: 8px; margin: 6px 0; }
            #${PANEL_ID} input, #${PANEL_ID} select { width: 100%; min-width: 0; padding: 5px 7px; border: 1px solid #bbb; border-radius: 5px; background: white; }
            #${PANEL_ID} .zoo-row input { width: 120px; }
            #${PANEL_ID} .zoo-actions { display: flex; flex-wrap: wrap; gap: 6px; margin: 11px 0; }
            #${PANEL_ID} button { cursor: pointer; border: 0; border-radius: 5px; padding: 6px 9px; background: #eee; color: #222; }
            #${PANEL_ID} #zoo-start { background: #ff6400; color: #fff; }
            #${PANEL_ID} #zoo-stop { background: #d93025; color: #fff; }
            #${PANEL_ID} #zoo-status { padding: 8px; border-radius: 5px; background: #f5f5f5; word-break: break-word; }
            #${PANEL_ID} #zoo-summary { margin-top: 7px; color: #666; }
        `;

        document.head.appendChild(style);
        document.body.appendChild(panel);
        panel.querySelector('#zoo-api-url').value = apiConfig.baseUrl || '';
        panel.querySelector('#zoo-api-token').value = apiConfig.apiToken || '';

        panel.querySelector('#zoo-load-groups').addEventListener('click', async () => {
            const baseUrl = normalizeBaseUrl(panel.querySelector('#zoo-api-url').value);
            const apiToken = panel.querySelector('#zoo-api-token').value.trim();
            saveApiConfig({ ...loadApiConfig(), baseUrl, apiToken });
            try {
                const groups = await loadBackendGroups();
                window.alert(`已加载 ${groups.length} 个后台分组`);
            } catch (error) {
                window.alert(error.message);
            }
        });
        panel.querySelector('#zoo-group').addEventListener('change', (event) => {
            saveApiConfig({ ...loadApiConfig(), selectedGroupId: event.target.value });
        });
        panel.querySelector('#zoo-start').addEventListener('click', startCollection);
        panel.querySelector('#zoo-stop').addEventListener('click', stopCollection);
        panel.querySelector('#zoo-export').addEventListener('click', () => {
            try {
                const state = loadState();
                downloadCsv(state.records, state, state.active || state.processedPages.length < (state.endPage - state.startPage + 1));
            } catch (error) {
                window.alert(error.message);
            }
        });

        updatePanel(loadState());
        if (apiConfig.baseUrl && apiConfig.apiToken) {
            void loadBackendGroups().catch((error) => {
                const state = loadState();
                state.currentStatus = `后台分组加载失败：${error.message}`;
                saveState(state);
            });
        }
    }

    function updatePanel(state) {
        const panel = document.getElementById(PANEL_ID);
        if (!panel) {
            return;
        }

        const input = panel.querySelector('#zoo-end-page');
        const status = panel.querySelector('#zoo-status');
        const summary = panel.querySelector('#zoo-summary');
        const startButton = panel.querySelector('#zoo-start');

        if (state.endPage && document.activeElement !== input) {
            input.value = state.endPage;
        }
        status.textContent = state.currentStatus || '等待开始';
        summary.textContent = `状态：${state.active ? '运行中' : '未运行'}｜分组：${state.groupName || '-'}｜已上传 ${state.uploadedPages.length} 页｜记录 ${state.records.length} 条`;
        startButton.disabled = state.active;
        startButton.style.opacity = state.active ? '0.55' : '1';
    }

    async function startCollection() {
        try {
            const panel = document.getElementById(PANEL_ID);
            const endPage = Number.parseInt(panel.querySelector('#zoo-end-page').value, 10);
            const groupSelect = panel.querySelector('#zoo-group');
            const groupId = Number.parseInt(groupSelect.value, 10);
            const groupName = groupSelect.selectedOptions[0]?.textContent || '';
            const baseUrl = normalizeBaseUrl(panel.querySelector('#zoo-api-url').value);
            const apiToken = panel.querySelector('#zoo-api-token').value.trim();
            saveApiConfig({ baseUrl, apiToken, selectedGroupId: groupSelect.value });
            if (!baseUrl || !apiToken) {
                throw new Error('请配置后台地址和 API Token');
            }
            if (!Number.isInteger(groupId)) {
                throw new Error('请先从后台加载并选择已有分组');
            }
            const { currentPage, totalPages } = await waitForPagination();

            if (!Number.isInteger(endPage)) {
                throw new Error('请输入有效的结束页码');
            }
            if (endPage < currentPage) {
                throw new Error(`结束页不能小于当前页 ${currentPage}`);
            }
            if (endPage > totalPages) {
                throw new Error(`结束页不能超过总页数 ${totalPages}`);
            }

            const state = {
                ...defaultState(),
                active: true,
                startPage: currentPage,
                endPage,
                groupId,
                groupName,
                currentStatus: `任务已开始：${groupName}，第 ${currentPage} 页到第 ${endPage} 页`,
                startedAt: new Date().toISOString()
            };
            saveState(state);
            void runCollector();
        } catch (error) {
            window.alert(error.message);
        }
    }

    function stopCollection() {
        const state = loadState();
        state.active = false;
        state.currentStatus = `用户已停止；保留 ${state.records.length} 条记录，可导出已有数据`;
        saveState(state);
    }

    let runnerActive = false;

    async function runCollector() {
        if (runnerActive) {
            return;
        }
        runnerActive = true;

        try {
            while (true) {
                let state = loadState();
                if (!state.active) {
                    break;
                }

                const { currentPage, totalPages } = await waitForPagination();
                if (state.endPage > totalPages) {
                    throw new Error(`任务结束页 ${state.endPage} 已超过当前显示的总页数 ${totalPages}`);
                }
                if (currentPage > state.endPage) {
                    throw new Error(`当前页 ${currentPage} 已超过任务结束页 ${state.endPage}`);
                }

                if (!state.processedPages.includes(currentPage)) {
                    state.currentStatus = `正在采集第 ${currentPage}/${state.endPage} 页，请勿手动翻页`;
                    saveState(state);

                    const pageRecords = await collectCurrentPage(currentPage);
                    state = loadState();
                    if (!state.active) {
                        break;
                    }

                    state.records = state.records
                        .filter((record) => record.sourcePage !== currentPage)
                        .concat(pageRecords);
                    saveState(state);

                    await uploadPageWithRetry(currentPage, pageRecords, state);
                    state = loadState();
                    if (!state.active) {
                        break;
                    }
                    state.processedPages = Array.from(new Set([...state.processedPages, currentPage])).sort((a, b) => a - b);
                    state.uploadedPages = Array.from(new Set([...state.uploadedPages, currentPage])).sort((a, b) => a - b);
                    state.currentStatus = `第 ${currentPage} 页完成并已上传：${pageRecords.length} 条`;
                    saveState(state);
                }

                state = loadState();
                if (!state.active) {
                    break;
                }

                if (currentPage === state.endPage) {
                    state.active = false;
                    state.currentStatus = `采集完成：${state.processedPages.length} 页，共 ${state.records.length} 条，正在导出 CSV`;
                    saveState(state);
                    downloadCsv(state.records, state, false);
                    clearState();
                    updatePanel({
                        ...defaultState(),
                        currentStatus: `采集完成并已导出：${state.records.length} 条记录`
                    });
                    break;
                }

                state.currentStatus = `第 ${currentPage} 页已保存，正在前往第 ${currentPage + 1} 页`;
                saveState(state);
                await goToNextPage(currentPage);
            }
        } catch (error) {
            const state = loadState();
            state.active = false;
            state.currentStatus = `采集失败：${error.message}；已保留 ${state.records.length} 条记录`;
            saveState(state);
            console.error('[1688 商品数据采集]', error);
        } finally {
            runnerActive = false;
        }
    }

    function initialize() {
        createPanel();
        const state = loadState();
        if (state.active) {
            state.currentStatus = '检测到未完成任务，准备继续采集';
            saveState(state);
            void runCollector();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
})();
