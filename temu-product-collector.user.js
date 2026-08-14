// ==UserScript==
// @name         Temu 商品与云启数据追加采集器
// @namespace    https://www.temu.com/
// @version      0.3.0
// @description  依赖云启数据扩展，采集 Temu 商品、销量、销售额、店铺信息，自动点击 See more，支持令牌上传与 CSV 导出
// @author       Zoo
// @match        https://www.temu.com/*
// @match        https://www.temu.com/
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_xmlhttpRequest
// @connect      *
// @run-at       document-start
// @sandbox      raw
// ==/UserScript==

(function () {
  "use strict";

  const SCRIPT_NAME = "Temu 商品采集器";
  const STORAGE_KEY = "zoo_temu_product_collector_state_v1";
  const CONFIG_KEY = "zoo_temu_product_collector_api_config_v1";
  const PANEL_ID = "zoo-temu-collector-panel";
  const STYLE_ID = "zoo-temu-collector-style";
  const SHADOW_REGISTRY_KEY = "__zooTemuCapturedShadowRootsV1";
  const DEFAULT_MAX_LOADS = 50;
  const LOAD_TIMEOUT_MS = 30000;
  const SEARCH_RESULT_TIMEOUT_MS = 8000;
  const YUNQI_RENDER_TIMEOUT_MS = 20000;
  const POST_LOAD_YUNQI_WAIT_MS = 5000;
  const MAX_NO_GROWTH_ROUNDS = 3;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // 必须在 document-start 执行。云启数据若在其他 JavaScript 隔离世界创建 closed ShadowRoot，
  // 浏览器可能不会经过本世界的 attachShadow；此时脚本会明确提示，绝不猜测扩展数据。
  function installShadowRootCapture() {
    if (!Array.isArray(window[SHADOW_REGISTRY_KEY])) {
      window[SHADOW_REGISTRY_KEY] = [];
    }

    const prototype = window.Element?.prototype;
    if (!prototype?.attachShadow || prototype.attachShadow.__zooTemuPatched) {
      return;
    }

    const originalAttachShadow = prototype.attachShadow;
    const patchedAttachShadow = function (options) {
      const root = originalAttachShadow.call(this, options);
      if (!window[SHADOW_REGISTRY_KEY].includes(root)) {
        window[SHADOW_REGISTRY_KEY].push(root);
      }
      return root;
    };

    Object.defineProperty(patchedAttachShadow, "__zooTemuPatched", {
      value: true,
    });
    Object.defineProperty(patchedAttachShadow, "toString", {
      value: () => originalAttachShadow.toString(),
    });
    prototype.attachShadow = patchedAttachShadow;
  }

  installShadowRootCapture();

  function defaultState() {
    return {
      active: false,
      status: "等待开始",
      records: [],
      successfulLoads: 0,
      duplicateCount: 0,
      currentSourcePage: 1,
      maxLoads: DEFAULT_MAX_LOADS,
      startedAt: null,
      updatedAt: null,
      stopReason: "",
      paused: false,
      pauseReason: "",
      keywords: [],
      keywordIndex: 0,
      currentKeyword: "",
      keywordLoads: 0,
      batchIndex: 0,
      pendingBatchRecords: [],
      pendingBatchPages: [],
      groupId: null,
      groupName: "",
      uploadedPages: [],
    };
  }

  function loadState() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      return saved && typeof saved === "object"
        ? { ...defaultState(), ...saved, active: false }
        : defaultState();
    } catch (_error) {
      return defaultState();
    }
  }

  let state = loadState();
  let stopRequested = false;

  function saveState() {
    state.updatedAt = new Date().toISOString();
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    updatePanel();
  }

  function loadApiConfig() {
    const defaults = {
      baseUrl: "",
      apiToken: "",
      selectedGroupId: "",
      groupNameInput: "",
      keywordsInput: "",
      sameGroup: true,
    };
    const saved = GM_getValue(CONFIG_KEY, null);
    if (saved && typeof saved === "object") {
      return { ...defaults, ...saved };
    }

    // 从旧版 Temu 域名 localStorage 迁移，避免升级脚本后丢失已有配置。
    try {
      const legacy = JSON.parse(localStorage.getItem(CONFIG_KEY) || "null");
      if (legacy && typeof legacy === "object") {
        const migrated = { ...defaults, ...legacy };
        GM_setValue(CONFIG_KEY, migrated);
        return migrated;
      }
    } catch (_error) {
      // 旧配置损坏时使用默认值。
    }
    return defaults;
  }

  function saveApiConfig(config) {
    GM_setValue(CONFIG_KEY, config);
  }

  function normalizeBaseUrl(value) {
    return cleanText(value).replace(/\/+$/, "");
  }

  function formatApiError(detail, fallback) {
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (!item || typeof item !== "object") {
            return String(item);
          }
          const location = Array.isArray(item.loc)
            ? item.loc.filter((part) => part !== "body").join(".")
            : "";
          const message = item.msg || item.message || JSON.stringify(item);
          return location ? `${location}：${message}` : message;
        })
        .join("；");
    }
    if (detail && typeof detail === "object") {
      return detail.message || JSON.stringify(detail);
    }
    return fallback;
  }

  function apiRequest(method, path, body = null) {
    const config = loadApiConfig();
    const baseUrl = normalizeBaseUrl(config.baseUrl);
    if (!baseUrl || !config.apiToken) {
      return Promise.reject(new Error("请配置后台地址和个人采集令牌"));
    }

    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: `${baseUrl}${path}`,
        headers: {
          Authorization: `Bearer ${config.apiToken}`,
          "Content-Type": "application/json",
        },
        data: body === null ? undefined : JSON.stringify(body),
        timeout: 30000,
        onload(response) {
          let parsed = null;
          try {
            parsed = response.responseText
              ? JSON.parse(response.responseText)
              : null;
          } catch (_error) {
            // 非 JSON 响应在错误信息中保留原始文本。
          }
          if (response.status >= 200 && response.status < 300) {
            resolve(parsed);
            return;
          }
          reject(
            new Error(
              formatApiError(
                parsed?.detail,
                response.responseText ||
                  `后台请求失败：HTTP ${response.status}`,
              ),
            ),
          );
        },
        ontimeout() {
          reject(new Error("后台请求超时，请检查地址和网络连接"));
        },
        onerror(response) {
          reject(
            new Error(
              `后台请求失败：${response?.error || response?.statusText || "网络错误"}`,
            ),
          );
        },
      });
    });
  }

  async function loadBackendGroups(selectedGroupId = null) {
    const groups = await apiRequest("GET", "/api/temu/ingest/groups");
    const select = document.querySelector(`#${PANEL_ID} #zoo-temu-group`);
    if (select) {
      const configured = selectedGroupId ?? loadApiConfig().selectedGroupId;
      select.innerHTML = '<option value="">请选择已有分组</option>';
      for (const group of groups) {
        const option = document.createElement("option");
        option.value = String(group.id);
        option.textContent = group.name;
        select.appendChild(option);
      }
      if (groups.some((group) => String(group.id) === String(configured)))
        select.value = String(configured);
    }
    return groups;
  }

  async function resolveCollectionGroup() {
    const panel = document.getElementById(PANEL_ID);
    const input = panel.querySelector("#zoo-temu-group-name");
    const select = panel.querySelector("#zoo-temu-group");
    const name = cleanText(input.value);
    let group;
    if (name)
      group = await apiRequest("POST", "/api/temu/ingest/groups", { name });
    else {
      const id = Number.parseInt(select.value, 10);
      if (!Number.isInteger(id))
        throw new Error("请输入分组名称，或选择已有 Temu 分组");
      group = { id, name: select.selectedOptions[0]?.textContent || "" };
    }
    saveApiConfig({
      ...loadApiConfig(),
      selectedGroupId: String(group.id),
      groupNameInput: group.name,
    });
    await loadBackendGroups(group.id);
    return group;
  }

  function cleanText(value) {
    return String(value || "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function normalizeCompactText(value) {
    return cleanText(value).replace(/\s+/g, "").toLowerCase();
  }

  function normalizeUrl(value) {
    const candidate = cleanText(value);
    if (!candidate) {
      return "";
    }
    try {
      const url = new URL(candidate, location.href);
      if (url.protocol !== "http:" && url.protocol !== "https:") {
        return "";
      }
      url.hash = "";
      return url.href;
    } catch (_error) {
      return "";
    }
  }

  function canonicalizeProductUrl(value, productId = "") {
    const normalized = normalizeUrl(value);
    if (productId) {
      const match = normalized.match(/^(https?:\/\/[^/]+)/i);
      const origin = match?.[1] || "https://www.temu.com";
      return `${origin}/g-${productId}.html`;
    }
    return normalized;
  }

  function canonicalizeImageUrl(value) {
    const normalized = normalizeUrl(value);
    if (!normalized) {
      return "";
    }
    try {
      const url = new URL(normalized);
      if (url.hostname.toLowerCase().endsWith("kwcdn.com")) {
        url.search = "";
      }
      url.hash = "";
      return url.href;
    } catch (_error) {
      return normalized;
    }
  }

  function firstText(root, selectors) {
    for (const selector of selectors) {
      const element = root?.querySelector?.(selector);
      const text = cleanText(element?.textContent);
      if (text) {
        return text;
      }
    }
    return "";
  }

  function firstAttribute(root, selectors, attributes) {
    for (const selector of selectors) {
      const element = root?.querySelector?.(selector);
      if (!element) {
        continue;
      }
      for (const attribute of attributes) {
        const value = cleanText(
          attribute === "currentSrc"
            ? element.currentSrc
            : element.getAttribute(attribute),
        );
        if (value) {
          return value;
        }
      }
    }
    return "";
  }

  function extractNativeImageUrl(card) {
    const mainImageUrl = canonicalizeImageUrl(
      firstAttribute(
        card,
        ['img[data-js-main-img="true"]'],
        ["currentSrc", "src", "data-src"],
      ),
    );
    if (mainImageUrl) {
      return mainImageUrl;
    }

    const videoPosterUrl = canonicalizeImageUrl(
      firstAttribute(card, ["video[poster]"], ["poster"]),
    );
    if (videoPosterUrl) {
      return videoPosterUrl;
    }

    return canonicalizeImageUrl(
      firstAttribute(
        card,
        ["img[data-cui-image]", ".goods-img-external img", "img"],
        ["currentSrc", "src", "data-src"],
      ),
    );
  }

  function extractLabeledValue(root, labelText) {
    const expected = normalizeCompactText(labelText).replace(/[：:]+$/, "");
    for (const label of root?.querySelectorAll?.(".label, span, div") || []) {
      const actual = normalizeCompactText(label.textContent).replace(
        /[：:]+$/,
        "",
      );
      if (actual !== expected) {
        continue;
      }
      const sibling = label.nextElementSibling;
      if (sibling) {
        return cleanText(sibling.textContent);
      }
      const parentText = cleanText(label.parentElement?.textContent);
      if (parentText) {
        return cleanText(parentText.replace(cleanText(label.textContent), ""));
      }
    }
    return "";
  }

  function parseScaledNumber(rawValue) {
    const original = cleanText(rawValue);
    if (!original) {
      return null;
    }
    const text = original
      .replace(/,/g, "")
      .replace(/[+$¥￥€£]/g, "")
      .toUpperCase();
    const match = text.match(/-?\d+(?:\.\d+)?\s*(K|M|B|W|万|亿)?/i);
    if (!match) {
      return null;
    }
    const value = Number.parseFloat(match[0]);
    if (!Number.isFinite(value)) {
      return null;
    }
    const unit = (match[1] || "").toUpperCase();
    const multiplier =
      {
        K: 1000,
        M: 1000000,
        B: 1000000000,
        W: 10000,
        万: 10000,
        亿: 100000000,
      }[unit] || 1;
    return Number((value * multiplier).toFixed(4));
  }

  function parseMoney(rawValue) {
    return parseScaledNumber(rawValue);
  }

  function extractMetric(text, label) {
    const source = cleanText(text);
    const pattern = new RegExp(
      `${label}\\s*([$¥￥€£]?\\s*[\\d,.]+\\s*(?:K|M|B|W|万|亿)?\\+?)`,
      "i",
    );
    return cleanText(source.match(pattern)?.[1] || "");
  }

  function extractTemuProductId(value) {
    const text = String(value || "");
    const patterns = [
      /-g-(\d+)\.html(?:[?#]|$)/i,
      /goodContainer-(\d+)/i,
      /goodsImage-(\d+)/i,
      /(?:goods_id|goodsId|product_id|productId|gdid)[=@:/"']+(\d+)/i,
    ];
    for (const pattern of patterns) {
      const id = text.match(pattern)?.[1];
      if (id) {
        return id;
      }
    }
    return "";
  }

  function getCapturedShadowRoots() {
    const roots = new Set();
    for (const root of window[SHADOW_REGISTRY_KEY] || []) {
      if (root?.querySelectorAll) {
        roots.add(root);
      }
    }

    const queue = [document, ...roots];
    for (const queryRoot of queue) {
      for (const element of queryRoot.querySelectorAll?.("*") || []) {
        if (element.shadowRoot && !roots.has(element.shadowRoot)) {
          roots.add(element.shadowRoot);
          queue.push(element.shadowRoot);
        }
      }
    }
    return Array.from(roots);
  }

  function getAllQueryRoots() {
    return [document, ...getCapturedShadowRoots()];
  }

  function queryAllAcrossRoots(selector) {
    const elements = [];
    const seen = new Set();
    for (const root of getAllQueryRoots()) {
      for (const element of root.querySelectorAll?.(selector) || []) {
        if (!seen.has(element)) {
          seen.add(element);
          elements.push(element);
        }
      }
    }
    return elements;
  }

  function findContainingNativeCard(element) {
    const selectors = [
      '[data-tooltip^="goodContainer-"]',
      'div[data-tooltip*="goodContainer-"]',
      'a[href*="-g-"][href*=".html"]',
    ];
    let current = element;
    while (current) {
      if (current.nodeType === Node.ELEMENT_NODE) {
        for (const selector of selectors) {
          if (current.matches?.(selector)) {
            return current.matches("a")
              ? current.closest("div") || current
              : current;
          }
        }
      }
      const root = current.getRootNode?.();
      current =
        current.parentElement ||
        (root instanceof ShadowRoot ? root.host : null);
    }
    return null;
  }

  function findNativeCards() {
    const candidates = queryAllAcrossRoots('[data-tooltip^="goodContainer-"]');
    if (candidates.length) {
      return candidates;
    }

    const cards = [];
    const seen = new Set();
    for (const link of queryAllAcrossRoots('a[href*="-g-"][href*=".html"]')) {
      const id = extractTemuProductId(link.href);
      if (!id || seen.has(id)) {
        continue;
      }
      const card =
        link.closest('[data-tooltip-title], [class*="goods"], article, li') ||
        link.parentElement;
      if (card) {
        seen.add(id);
        cards.push(card);
      }
    }
    return cards;
  }

  function extractNativeProduct(card) {
    const link = card.matches?.('a[href*="-g-"]')
      ? card
      : card.querySelector?.('a[href*="-g-"][href*=".html"]');
    const tooltipValues = [
      card.getAttribute?.("data-tooltip"),
      card.getAttribute?.("data-tooltip-title"),
      link?.getAttribute("href"),
    ].filter(Boolean);
    const nativeProductId = extractTemuProductId(tooltipValues.join(" "));
    const productUrl = canonicalizeProductUrl(
      link?.href || "",
      nativeProductId,
    );
    const title = cleanText(
      card.getAttribute?.("data-tooltip-title") ||
        firstText(card, [
          "h2 span[aria-hidden='true']",
          "h2",
          "[data-tooltip-title]",
        ]) ||
        link?.getAttribute("title"),
    );
    const salePriceRaw = firstText(card, [
      '[data-type="price"] > span:not([aria-hidden="true"])',
      '[data-type="price"] ._2XgTiMJi',
      '[data-type="price"]',
    ]);
    const originalPriceRaw = firstText(card, [
      '[data-type="marketPrice"] span[aria-hidden="true"]',
      '[data-type="marketPrice"]',
    ]).replace(/^Original price\s*/i, "");
    const soldRaw = firstText(card, [
      '[data-type="saleTips"] ._2XgTiMJi',
      '[data-type="saleTips"]',
    ]);
    const ratingRaw = firstText(card, [
      '[aria-label*="out of five stars"]',
      '[aria-label*="stars"]',
    ]);
    const reviewRaw = firstText(card, ["._3cWlbpFG", '[class*="review"]']);
    const imageUrl = extractNativeImageUrl(card);
    const cardText = cleanText(card.textContent);
    const exactElementTexts = new Set(
      Array.from(card.querySelectorAll("span, div"), (element) =>
        cleanText(element.textContent).toLowerCase(),
      ),
    );
    const tags = [];
    for (const label of ["Local", "Top pick"]) {
      if (
        exactElementTexts.has(label.toLowerCase()) ||
        new RegExp(`(?:^|\\s)${label}(?:\\s|$)`, "i").test(cardText)
      ) {
        tags.push(label);
      }
    }
    const isAd = exactElementTexts.has("ad");
    if (isAd) {
      tags.push("AD");
    }

    return {
      nativeProductId,
      productUrl,
      title,
      imageUrl,
      nativeSalePriceRaw: salePriceRaw,
      nativeSalePrice: parseMoney(salePriceRaw),
      originalPriceRaw,
      originalPrice: parseMoney(originalPriceRaw),
      temuSoldRaw: soldRaw,
      temuSold: parseScaledNumber(soldRaw),
      nativeRatingRaw: ratingRaw,
      nativeRating: parseScaledNumber(ratingRaw),
      nativeReviewCountRaw: reviewRaw,
      nativeReviewCount: parseScaledNumber(reviewRaw),
      stockTip: firstText(card, [
        '[data-tag-id] [aria-hidden="true"]',
        '[class*="tag"]',
      ]),
      deliveryTip: firstText(card, [
        '[class*="Rxlst"] span',
        '[class*="delivery"]',
      ]),
      nativeTags: tags.join("|"),
      isAd: isAd ? "是" : "否",
    };
  }

  function extractYunqiProduct(card) {
    const productId = cleanText(card.getAttribute("gdid"));
    const mallId = cleanText(card.getAttribute("mallid"));
    const salePriceRaw = cleanText(card.getAttribute("gdprice"));
    const totalSalesAttributeRaw = cleanText(card.getAttribute("goodsale"));
    const salesRaw = firstText(card, [".zxlitem .value"]);
    const revenueRaw = firstText(card, [".xseitem .value"]);
    const ratingReviewRaw = firstText(card, [".usmoplsitem .value"]);
    const ratingMatch = ratingReviewRaw.match(/([\d.]+)\s*\/\s*([\d,.]+)/);
    const dailySalesRaw = extractMetric(salesRaw, "日");
    const dailySales = parseScaledNumber(dailySalesRaw);
    const dailyRevenueRaw = extractMetric(revenueRaw, "日");
    const dailyRevenue = parseMoney(dailyRevenueRaw);
    const parsedSalePrice = parseMoney(salePriceRaw);
    const shopNameElement = card.querySelector(
      ".toyqsjshopinfo .shoplogo .value",
    );
    const shopName = cleanText(shopNameElement?.textContent);

    const weeklySalesRaw = extractMetric(salesRaw, "周");
    const monthlySalesRaw = extractMetric(salesRaw, "月");
    const totalSalesRaw =
      extractMetric(salesRaw, "总") || totalSalesAttributeRaw;
    const weeklyRevenueRaw = extractMetric(revenueRaw, "周");
    const monthlyRevenueRaw = extractMetric(revenueRaw, "月");
    const totalRevenueRaw = extractMetric(revenueRaw, "总");

    return {
      yunqiProductId: productId,
      mallId,
      yunqiSalePriceRaw: salePriceRaw,
      yunqiSalePrice: parsedSalePrice,
      category:
        firstText(card, [".spitems .flitem .value"]) ||
        extractLabeledValue(card, "分类"),
      dailySalesRaw,
      dailySales,
      dailyRevenueRaw,
      dailyRevenue,
      weeklySalesRaw,
      weeklySales: parseScaledNumber(weeklySalesRaw),
      monthlySalesRaw,
      monthlySales: parseScaledNumber(monthlySalesRaw),
      totalSalesRaw,
      totalSales: parseScaledNumber(totalSalesRaw),
      weeklyRevenueRaw,
      weeklyRevenue: parseMoney(weeklyRevenueRaw),
      monthlyRevenueRaw,
      monthlyRevenue: parseMoney(monthlyRevenueRaw),
      totalRevenueRaw,
      totalRevenue: parseMoney(totalRevenueRaw),
      listingDate:
        firstText(card, [".sjsjitem .value"]) ||
        extractLabeledValue(card, "上架时间"),
      yunqiRatingRaw: cleanText(ratingMatch?.[1] || ""),
      yunqiRating: parseScaledNumber(ratingMatch?.[1]),
      yunqiReviewCountRaw: cleanText(ratingMatch?.[2] || ""),
      yunqiReviewCount: parseScaledNumber(ratingMatch?.[2]),
      shopName,
      shopType: extractLabeledValue(
        card.querySelector(".toyqsjshopinfo") || card,
        "店铺类型",
      ),
      shopOpenedAt:
        firstText(card, [".kdsjtime .value"]) ||
        extractLabeledValue(card, "开店时间"),
      shopTotalSalesRaw: firstText(card, [".dpzxl .value"]),
      shopTotalSales: parseScaledNumber(firstText(card, [".dpzxl .value"])),
      yunqiIndex: cleanText(card.getAttribute("index")),
    };
  }

  function findYunqiCards() {
    return queryAllAcrossRoots(".yqtop.goods-card[gdid], .goods-card[gdid]");
  }

  function createEmptyNativeProduct() {
    return {
      nativeProductId: "",
      productUrl: "",
      title: "",
      imageUrl: "",
      nativeSalePriceRaw: "",
      nativeSalePrice: null,
      originalPriceRaw: "",
      originalPrice: null,
      temuSoldRaw: "",
      temuSold: null,
      nativeRatingRaw: "",
      nativeRating: null,
      nativeReviewCountRaw: "",
      nativeReviewCount: null,
      stockTip: "",
      deliveryTip: "",
      nativeTags: "",
      isAd: "否",
    };
  }

  function createEmptyYunqiProduct() {
    return {
      yunqiProductId: "",
      mallId: "",
      yunqiSalePriceRaw: "",
      yunqiSalePrice: null,
      category: "",
      dailySalesRaw: "",
      dailySales: null,
      dailyRevenueRaw: "",
      dailyRevenue: null,
      weeklySalesRaw: "",
      weeklySales: null,
      monthlySalesRaw: "",
      monthlySales: null,
      totalSalesRaw: "",
      totalSales: null,
      weeklyRevenueRaw: "",
      weeklyRevenue: null,
      monthlyRevenueRaw: "",
      monthlyRevenue: null,
      totalRevenueRaw: "",
      totalRevenue: null,
      listingDate: "",
      yunqiRatingRaw: "",
      yunqiRating: null,
      yunqiReviewCountRaw: "",
      yunqiReviewCount: null,
      shopName: "",
      shopType: "",
      shopOpenedAt: "",
      shopTotalSalesRaw: "",
      shopTotalSales: null,
      yunqiIndex: "",
    };
  }

  function associateProducts() {
    const nativeCards = findNativeCards();
    const yunqiCards = findYunqiCards();
    const nativeProducts = nativeCards.map((card, index) => ({
      card,
      index,
      data: extractNativeProduct(card),
    }));
    const yunqiProducts = yunqiCards.map((card, index) => ({
      card,
      index,
      data: extractYunqiProduct(card),
    }));

    const nativeById = new Map(
      nativeProducts
        .filter((item) => item.data.nativeProductId)
        .map((item) => [item.data.nativeProductId, item]),
    );
    const usedNative = new Set();
    const associated = [];

    for (const yunqi of yunqiProducts) {
      let native = null;
      let associationStatus = "仅云启数据";
      let associationReason = "未找到可可靠关联的 Temu 原生商品卡片";

      if (
        yunqi.data.yunqiProductId &&
        nativeById.has(yunqi.data.yunqiProductId)
      ) {
        native = nativeById.get(yunqi.data.yunqiProductId);
        associationStatus = "商品ID匹配";
        associationReason = "gdid 与 Temu 链接商品 ID 一致";
      } else {
        const containingCard = findContainingNativeCard(
          yunqi.card.getRootNode()?.host || yunqi.card,
        );
        if (containingCard) {
          const containingData = extractNativeProduct(containingCard);
          if (containingData.nativeProductId) {
            native = { card: containingCard, data: containingData };
            associationStatus = "容器关联";
            associationReason = yunqi.data.yunqiProductId
              ? `云启 Shadow DOM 宿主位于对应 Temu 商品卡片内；gdid=${yunqi.data.yunqiProductId} 仅作辅助字段`
              : "云启 Shadow DOM 宿主位于对应 Temu 商品卡片内";
          } else {
            associationReason =
              "已定位云启数据容器，但无法从对应 Temu 卡片提取原生商品 ID";
          }
        }
      }

      if (!native) {
        const indexedNative = nativeProducts[yunqi.index];
        if (indexedNative && !usedNative.has(indexedNative)) {
          const nativeId = indexedNative.data.nativeProductId;
          const yunqiId = yunqi.data.yunqiProductId;
          if (!nativeId || !yunqiId || nativeId === yunqiId) {
            native = indexedNative;
            associationStatus = "顺序匹配";
            associationReason = "两类卡片顺序一致，且商品 ID 不冲突";
          }
        }
      }

      if (native) {
        usedNative.add(native);
      }
      associated.push({
        ...createEmptyNativeProduct(),
        ...(native?.data || {}),
        ...createEmptyYunqiProduct(),
        ...yunqi.data,
        associationStatus,
        associationReason,
      });
    }

    for (const native of nativeProducts) {
      if (usedNative.has(native)) {
        continue;
      }
      associated.push({
        ...createEmptyNativeProduct(),
        ...native.data,
        ...createEmptyYunqiProduct(),
        associationStatus: "仅Temu原生数据",
        associationReason: "云启数据尚未渲染或 closed ShadowRoot 无法访问",
      });
    }

    return {
      records: associated,
      nativeCardCount: nativeProducts.length,
      yunqiCardCount: yunqiProducts.length,
      capturedShadowRootCount: getCapturedShadowRoots().length,
    };
  }

  function getRecordKey(record) {
    if (record.nativeProductId) {
      return `temu:${record.nativeProductId}`;
    }
    if (record.productUrl) {
      return `url:${canonicalizeProductUrl(record.productUrl)}`;
    }
    return "";
  }

  function mergeCollectedRecords(foundRecords, sourcePage) {
    const byKey = new Map();
    for (const existing of state.records) {
      const key = getRecordKey(existing);
      if (key) {
        byKey.set(key, existing);
      }
    }

    let added = 0;
    let duplicates = 0;
    for (const record of foundRecords) {
      const key = getRecordKey(record);
      if (!key) {
        continue;
      }
      const existing = byKey.get(key);
      if (existing) {
        duplicates += 1;
        Object.assign(existing, record, {
          imageUrl: record.imageUrl || existing.imageUrl || "",
          sourcePage: existing.sourcePage,
          firstSeenAt: existing.firstSeenAt,
          lastSeenAt: new Date().toISOString(),
        });
        continue;
      }
      const now = new Date().toISOString();
      const stored = {
        ...record,
        sourcePage,
        firstSeenAt: now,
        lastSeenAt: now,
      };
      state.records.push(stored);
      byKey.set(key, stored);
      added += 1;
    }
    state.duplicateCount += duplicates;
    return { added, duplicates };
  }

  function uploadableRecords(records) {
    return records.filter(
      (record) =>
        record.nativeProductId &&
        record.yunqiProductId &&
        ["商品ID匹配", "容器关联"].includes(record.associationStatus),
    );
  }

  function toUploadProduct(record) {
    return {
      temu_product_id: record.nativeProductId,
      yunqi_gdid: record.yunqiProductId || null,
      mall_id: record.mallId || null,
      title: record.title,
      product_url: record.productUrl,
      image_url: record.imageUrl || null,
      sale_price: record.yunqiSalePrice ?? record.nativeSalePrice ?? null,
      original_price: record.originalPrice ?? null,
      daily_sales: record.dailySales ?? 0,
      weekly_sales: record.weeklySales ?? 0,
      monthly_sales: record.monthlySales ?? 0,
      total_sales: record.totalSales ?? 0,
      daily_revenue: record.dailyRevenue ?? null,
      weekly_revenue: record.weeklyRevenue ?? null,
      monthly_revenue: record.monthlyRevenue ?? null,
      total_revenue: record.totalRevenue ?? null,
      listing_date: record.listingDate || null,
      category: record.category || null,
      rating: record.yunqiRating ?? record.nativeRating ?? null,
      review_count: record.yunqiReviewCount ?? record.nativeReviewCount ?? null,
      shop_name: record.shopName || null,
      shop_type: record.shopType || null,
      shop_opened_at: record.shopOpenedAt || null,
      shop_total_sales: record.shopTotalSales ?? null,
      temu_tags: record.nativeTags || null,
      is_ad: record.isAd === "是",
      delivery_tip: record.deliveryTip || null,
      association_status: record.associationStatus,
    };
  }

  async function uploadPageWithRetry(sourcePage, records) {
    const unique = new Map();
    for (const record of uploadableRecords(records)) {
      unique.set(record.nativeProductId, record);
    }
    const accepted = [...unique.values()];
    if (!accepted.length)
      throw new Error(`第 ${sourcePage} 页没有可靠关联、可上传的商品`);
    const keywordKey = encodeURIComponent(
      state.currentKeyword || "manual",
    ).slice(0, 40);
    const payload = {
      idempotency_key: `temu-${state.startedAt}-${state.groupId}-${keywordKey}-batch-${state.batchIndex}`,
      group_id: state.groupId,
      source_page: sourcePage,
      products: accepted.map(toUploadProduct),
    };
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      try {
        state.status = `第 ${sourcePage} 页上传 ${accepted.length} 条（${attempt}/5）`;
        saveState();
        const result = await apiRequest(
          "POST",
          "/api/temu/ingest/batches",
          payload,
        );
        if (!state.uploadedPages.includes(sourcePage))
          state.uploadedPages.push(sourcePage);
        saveState();
        return result;
      } catch (error) {
        if (attempt === 5) throw error;
        await sleep(Math.min(30000, 2000 * 2 ** (attempt - 1)));
      }
    }
  }

  function parseKeywords(value) {
    return [
      ...new Set(
        String(value || "")
          .split(/\r?\n/)
          .map(cleanText)
          .filter(Boolean),
      ),
    ];
  }

  function searchInput() {
    return document.querySelector(
      '#searchBar #searchInput, input#searchInput[type="search"]',
    );
  }

  async function submitKeywordSearch(keyword) {
    const input = searchInput();
    if (!input) throw new Error("未找到 Temu 搜索输入框");
    input.focus();
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )?.set;
    setter?.call(input, keyword);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    const button = document.querySelector(
      '#searchBar [role="button"][aria-label="Submit search"]',
    );
    if (button) button.click();
    else
      input.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Enter",
          code: "Enter",
          bubbles: true,
        }),
      );
    const startedAt = Date.now();
    while (Date.now() - startedAt < SEARCH_RESULT_TIMEOUT_MS) {
      if (findNativeCards().length > 0) return;
      if (stopRequested) return;
      await sleep(250);
    }
    state.paused = true;
    state.pauseReason = `关键词“${keyword}”搜索 8 秒后仍无商品，可能遇到人机验证`;
    state.status = `${state.pauseReason}。处理验证后点击继续`;
    saveState();
    window.alert(state.status);
    throw new Error("COLLECTOR_PAUSED");
  }

  async function resolveKeywordGroup(keyword, sameGroup) {
    if (sameGroup) return resolveCollectionGroup();
    const group = await apiRequest("POST", "/api/temu/ingest/groups", {
      name: keyword,
    });
    state.groupId = group.id;
    state.groupName = group.name;
    return group;
  }

  async function flushPendingBatch() {
    if (!state.pendingBatchRecords.length) return;
    state.batchIndex += 1;
    const sourcePage = Math.max(...state.pendingBatchPages, 1);
    await uploadPageWithRetry(sourcePage, state.pendingBatchRecords);
    state.pendingBatchRecords = [];
    state.pendingBatchPages = [];
    saveState();
  }

  function queueBatchPage(sourcePage, records) {
    state.pendingBatchPages.push(sourcePage);
    state.pendingBatchRecords.push(...records);
  }

  function isSeeMoreButton(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) {
      return false;
    }
    const ariaLabel = cleanText(element.getAttribute("aria-label"));
    const text = cleanText(element.textContent);
    return (
      /see more items/i.test(ariaLabel) ||
      (/see more/i.test(text) && element.getAttribute("role") === "button")
    );
  }

  function findSeeMoreButton() {
    for (const root of getAllQueryRoots()) {
      const exact = root.querySelector?.(
        '[role="button"][aria-label="See more items"]',
      );
      if (exact) {
        return exact;
      }
      for (const candidate of root.querySelectorAll?.(
        '[role="button"], button',
      ) || []) {
        if (isSeeMoreButton(candidate)) {
          return candidate;
        }
      }
    }
    return null;
  }

  function isButtonUnavailable(button) {
    return (
      !button ||
      button.getAttribute("aria-disabled") === "true" ||
      button.hasAttribute("disabled") ||
      /disabled|forbid/i.test(button.className || "")
    );
  }

  async function waitForYunqiData(
    expectedMinimum = 1,
    timeoutMs = YUNQI_RENDER_TIMEOUT_MS,
  ) {
    const startedAt = Date.now();
    let latest = associateProducts();
    while (Date.now() - startedAt < timeoutMs) {
      if (latest.yunqiCardCount >= expectedMinimum) {
        return latest;
      }
      await sleep(500);
      latest = associateProducts();
    }
    return latest;
  }

  async function waitForNewProducts(previousKeys, timeoutMs = LOAD_TIMEOUT_MS) {
    const startedAt = Date.now();
    let stableRounds = 0;
    let previousVisibleCount = previousKeys.size;
    let latest = associateProducts();

    while (Date.now() - startedAt < timeoutMs) {
      if (stopRequested) {
        return { ...latest, newUniqueCount: 0, stopped: true };
      }
      await sleep(750);
      latest = associateProducts();
      const keys = new Set(latest.records.map(getRecordKey).filter(Boolean));
      const newUniqueCount = Array.from(keys).filter(
        (key) => !previousKeys.has(key),
      ).length;
      if (newUniqueCount > 0) {
        await sleep(1500);
        latest = associateProducts();
        const settledKeys = new Set(
          latest.records.map(getRecordKey).filter(Boolean),
        );
        return {
          ...latest,
          newUniqueCount: Array.from(settledKeys).filter(
            (key) => !previousKeys.has(key),
          ).length,
        };
      }

      if (keys.size === previousVisibleCount) {
        stableRounds += 1;
      } else {
        stableRounds = 0;
        previousVisibleCount = keys.size;
      }
      if (stableRounds >= 6) {
        break;
      }
    }
    return { ...latest, newUniqueCount: 0 };
  }

  function currentVisibleKeys() {
    return new Set(
      associateProducts().records.map(getRecordKey).filter(Boolean),
    );
  }

  async function runCollector() {
    if (state.active) return;
    const panel = document.getElementById(PANEL_ID);
    const config = {
      ...loadApiConfig(),
      baseUrl: panel.querySelector("#zoo-temu-api-url").value,
      apiToken: panel.querySelector("#zoo-temu-api-token").value,
      groupNameInput: panel.querySelector("#zoo-temu-group-name").value,
      selectedGroupId: panel.querySelector("#zoo-temu-group").value,
      keywordsInput: panel.querySelector("#zoo-temu-keywords").value,
      sameGroup: panel.querySelector("#zoo-temu-same-group").checked,
    };
    const keywords = parseKeywords(config.keywordsInput);
    if (!keywords.length) {
      state.status = "请至少输入一个搜索关键词";
      saveState();
      return;
    }
    saveApiConfig(config);
    const maxLoads = Number.parseInt(
      panel.querySelector("#zoo-temu-max-loads").value,
      10,
    );
    state.maxLoads =
      Number.isInteger(maxLoads) && maxLoads >= 0
        ? maxLoads
        : DEFAULT_MAX_LOADS;
    if (!state.paused) {
      Object.assign(state, {
        keywords,
        keywordIndex: 0,
        currentKeyword: "",
        keywordLoads: 0,
        successfulLoads: 0,
        batchIndex: 0,
        pendingBatchRecords: [],
        pendingBatchPages: [],
        uploadedPages: [],
        startedAt: new Date().toISOString(),
        stopReason: "",
      });
    }
    state.paused = false;
    state.pauseReason = "";
    state.active = true;
    stopRequested = false;
    saveState();
    try {
      for (
        ;
        state.keywordIndex < state.keywords.length;
        state.keywordIndex += 1
      ) {
        const keyword = state.keywords[state.keywordIndex];
        state.currentKeyword = keyword;
        state.keywordLoads = 0;
        state.pendingBatchRecords = [];
        state.pendingBatchPages = [];
        const group = await resolveKeywordGroup(keyword, config.sameGroup);
        state.groupId = group.id;
        state.groupName = group.name;
        state.status = `正在搜索 ${state.keywordIndex + 1}/${state.keywords.length}：${keyword}`;
        saveState();
        await submitKeywordSearch(keyword);
        const initial = await waitForYunqiData(1, SEARCH_RESULT_TIMEOUT_MS);
        if (!initial.yunqiCardCount)
          throw new Error(`关键词“${keyword}”未读取到云启数据`);
        mergeCollectedRecords(initial.records, 1);
        queueBatchPage(1, initial.records);
        let noGrowthRounds = 0;
        while (!stopRequested && state.keywordLoads < state.maxLoads) {
          window.scrollTo({
            top: document.documentElement.scrollHeight,
            behavior: "auto",
          });
          await sleep(800);
          const button = findSeeMoreButton();
          if (isButtonUnavailable(button)) break;
          const beforeKeys = currentVisibleKeys();
          button.scrollIntoView({ block: "center", behavior: "auto" });
          await sleep(300);
          button.click();
          const loaded = await waitForNewProducts(beforeKeys);
          if (loaded.stopped || stopRequested) break;
          if (loaded.newUniqueCount <= 0) {
            noGrowthRounds += 1;
            if (noGrowthRounds >= MAX_NO_GROWTH_ROUNDS) break;
            continue;
          }
          noGrowthRounds = 0;
          state.keywordLoads += 1;
          state.successfulLoads += 1;
          state.currentSourcePage = state.keywordLoads + 1;
          await sleep(POST_LOAD_YUNQI_WAIT_MS);
          const settled = associateProducts();
          mergeCollectedRecords(settled.records, state.currentSourcePage);
          queueBatchPage(state.currentSourcePage, settled.records);
          if (state.keywordLoads % 2 === 0) await flushPendingBatch();
          state.status = `关键词“${keyword}”已翻页 ${state.keywordLoads}/${state.maxLoads}`;
          saveState();
        }
        await flushPendingBatch();
        if (stopRequested) break;
      }
      state.stopReason = stopRequested ? "用户手动停止" : "全部关键词已完成";
      state.status = `采集结束：${state.stopReason}`;
    } catch (error) {
      if (error.message !== "COLLECTOR_PAUSED") {
        state.stopReason = error.message;
        state.status = `采集失败：${error.message}`;
        console.error(`[${SCRIPT_NAME}]`, error);
      }
    } finally {
      state.active = false;
      saveState();
    }
  }

  function continueCollector() {
    if (!state.paused || state.active) return;
    runCollector();
  }

  function stopCollector() {
    stopRequested = true;
    state.status = "正在停止，等待当前检测结束";
    saveState();
  }

  async function savePanelConfig() {
    const panel = document.getElementById(PANEL_ID);
    if (!panel) {
      return;
    }

    const maxLoads = Number.parseInt(
      panel.querySelector("#zoo-temu-max-loads").value,
      10,
    );
    state.maxLoads =
      Number.isInteger(maxLoads) && maxLoads >= 0
        ? maxLoads
        : DEFAULT_MAX_LOADS;

    const config = {
      ...loadApiConfig(),
      baseUrl: normalizeBaseUrl(panel.querySelector("#zoo-temu-api-url").value),
      apiToken: cleanText(panel.querySelector("#zoo-temu-api-token").value),
      groupNameInput: cleanText(
        panel.querySelector("#zoo-temu-group-name").value,
      ),
      selectedGroupId: panel.querySelector("#zoo-temu-group").value,
      keywordsInput: panel.querySelector("#zoo-temu-keywords").value,
      sameGroup: panel.querySelector("#zoo-temu-same-group").checked,
    };
    if (!config.baseUrl || !config.apiToken) {
      state.status = "请填写后台地址，并粘贴与 1688 相同的个人采集令牌";
      saveState();
      return;
    }

    saveApiConfig(config);
    panel.querySelector("#zoo-temu-api-url").value = config.baseUrl;
    panel.querySelector("#zoo-temu-api-token").value = config.apiToken;
    panel.querySelector("#zoo-temu-group-name").value = config.groupNameInput;
    panel.querySelector("#zoo-temu-keywords").value = config.keywordsInput;
    panel.querySelector("#zoo-temu-same-group").checked = config.sameGroup;
    panel.querySelector("#zoo-temu-max-loads").value = String(state.maxLoads);

    state.status = "配置已保存，正在验证统一令牌并加载 Temu 分组";
    saveState();
    try {
      const groups = await loadBackendGroups(config.selectedGroupId);
      state.status = `配置有效，已加载 ${groups.length} 个 Temu 分组`;
    } catch (error) {
      state.status = `配置已保存，但 Temu 分组加载失败：${error.message}`;
    }
    saveState();
  }

  function csvEscape(value) {
    const text = String(value ?? "");
    return `"${text.replace(/"/g, '""')}"`;
  }

  const CSV_COLUMNS = [
    ["采集开始时间", "collectionStartedAt"],
    ["首次发现时间", "firstSeenAt"],
    ["最后发现时间", "lastSeenAt"],
    ["来源页码", "sourcePage"],
    ["总成功翻页次数", "finalSuccessfulLoads"],
    ["Temu原生商品ID", "nativeProductId"],
    ["店铺ID_mallid", "mallId"],
    ["标题", "title"],
    ["商品链接", "productUrl"],
    ["图片链接", "imageUrl"],
    ["售价", "resolvedSalePrice"],
    ["原价", "originalPrice"],
    ["日销量", "dailySales"],
    ["周销量", "weeklySales"],
    ["月销量", "monthlySales"],
    ["总销量", "totalSales"],
    ["日销售额", "dailyRevenue"],
    ["周销售额", "weeklyRevenue"],
    ["月销售额", "monthlyRevenue"],
    ["总销售额", "totalRevenue"],
    ["上架时间", "listingDate"],
    ["分类", "category"],
    ["评分", "resolvedRating"],
    ["评论数", "resolvedReviewCount"],
    ["店铺名称", "shopName"],
    ["店铺类型", "shopType"],
    ["开店时间", "shopOpenedAt"],
    ["店铺总销量", "shopTotalSales"],
    ["店铺总销量原文", "shopTotalSalesRaw"],
    ["Temu标签", "nativeTags"],
    ["是否广告", "isAd"],
    ["配送提示", "deliveryTip"],
    ["云启卡片索引", "yunqiIndex"],
  ];

  function prepareCsvRecord(record) {
    const hasYunqiPrice =
      record.yunqiSalePrice !== null && record.yunqiSalePrice !== undefined;
    const hasYunqiRating =
      record.yunqiRating !== null && record.yunqiRating !== undefined;
    const hasYunqiReview =
      record.yunqiReviewCount !== null && record.yunqiReviewCount !== undefined;
    return {
      ...record,
      collectionStartedAt: state.startedAt || "",
      finalSuccessfulLoads: state.successfulLoads,
      resolvedSalePrice: hasYunqiPrice
        ? record.yunqiSalePrice
        : record.nativeSalePrice,
      resolvedSalePriceRaw: hasYunqiPrice
        ? record.yunqiSalePriceRaw
        : record.nativeSalePriceRaw,
      resolvedRating: hasYunqiRating ? record.yunqiRating : record.nativeRating,
      resolvedRatingRaw: hasYunqiRating
        ? record.yunqiRatingRaw
        : record.nativeRatingRaw,
      resolvedReviewCount: hasYunqiReview
        ? record.yunqiReviewCount
        : record.nativeReviewCount,
      resolvedReviewCountRaw: hasYunqiReview
        ? record.yunqiReviewCountRaw
        : record.nativeReviewCountRaw,
      dailyRevenue: record.dailyRevenue ?? "",
      dailyRevenueRaw: record.dailyRevenueRaw || "",
    };
  }

  function exportCsv() {
    if (!state.records.length) {
      state.status = "没有可导出的记录";
      saveState();
      return;
    }

    const rows = [
      CSV_COLUMNS.map(([label]) => label),
      ...state.records.map((record) => {
        const prepared = prepareCsvRecord(record);
        return CSV_COLUMNS.map(([, key]) => prepared[key] ?? "");
      }),
    ];
    const csv = `\uFEFF${rows.map((row) => row.map(csvEscape).join(",")).join("\r\n")}`;
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    anchor.href = url;
    anchor.download = `Temu商品数据_${state.records.length}条_翻页${state.successfulLoads}次_${timestamp}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    state.status = `已导出 ${state.records.length} 条 CSV`;
    saveState();
  }

  function clearCollectorState() {
    if (state.active) {
      stopCollector();
      return;
    }
    localStorage.removeItem(STORAGE_KEY);
    state = defaultState();
    stopRequested = false;
    saveState();
  }

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) {
      return;
    }
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      #${PANEL_ID} {
        position: fixed;
        right: 18px;
        top: 90px;
        z-index: 2147483647;
        width: 320px;
        padding: 14px;
        border: 1px solid #d8d8d8;
        border-radius: 12px;
        background: rgba(255, 255, 255, 0.98);
        box-shadow: 0 8px 30px rgba(0, 0, 0, 0.18);
        color: #222;
        font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      #${PANEL_ID} * { box-sizing: border-box; }
      #${PANEL_ID} .zoo-title { font-size: 16px; font-weight: 700; margin-bottom: 10px; }
      #${PANEL_ID} .zoo-warning {
        padding: 8px;
        margin-bottom: 10px;
        border-radius: 6px;
        background: #fff7e6;
        color: #8a5200;
      }
      #${PANEL_ID} .zoo-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px 12px;
        margin-bottom: 10px;
      }
      #${PANEL_ID} .zoo-value { font-weight: 700; text-align: right; }
      #${PANEL_ID} .zoo-field { display: grid; gap: 4px; margin: 8px 0; }
      #${PANEL_ID} input, #${PANEL_ID} select, #${PANEL_ID} textarea { width: 100%; padding: 6px 7px; border: 1px solid #bbb; border-radius: 5px; }
      #${PANEL_ID} textarea { min-height: 78px; resize: vertical; }
      #${PANEL_ID} .zoo-check { display: flex; gap: 7px; align-items: center; margin: 8px 0; }
      #${PANEL_ID} .zoo-check input { width: auto; }
      #${PANEL_ID} .zoo-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
      #${PANEL_ID} button {
        padding: 7px 8px;
        border: 0;
        border-radius: 6px;
        cursor: pointer;
        background: #222;
        color: #fff;
      }
      #${PANEL_ID} button.zoo-save { background: #175cd3; }
      #${PANEL_ID} button.zoo-stop { background: #b42318; }
      #${PANEL_ID} button.zoo-export { background: #087443; }
      #${PANEL_ID} button.zoo-clear { background: #666; }
      #${PANEL_ID} button:disabled { cursor: not-allowed; opacity: 0.45; }
      #${PANEL_ID} .zoo-status {
        max-height: 110px;
        overflow: auto;
        margin-top: 10px;
        padding: 8px;
        border-radius: 6px;
        background: #f4f4f4;
        word-break: break-word;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  function createPanel() {
    if (!document.body || document.getElementById(PANEL_ID)) {
      return;
    }
    injectStyles();
    const panel = document.createElement("section");
    panel.id = PANEL_ID;
    panel.innerHTML = `
      <div class="zoo-title">Temu 商品采集器</div>
      <div class="zoo-warning">依赖云启数据扩展。未读取到云启卡片时不会猜测销量、销售额、上架或店铺数据。</div>
      <div class="zoo-grid">
        <span>已采集</span><span class="zoo-value" data-value="records">0</span>
        <span>成功翻页</span><span class="zoo-value" data-value="loads">0</span>
        <span>当前来源页</span><span class="zoo-value" data-value="page">1</span>
        <span>重复检测</span><span class="zoo-value" data-value="duplicates">0</span>
        <span>ShadowRoot</span><span class="zoo-value" data-value="shadows">0</span>
        <span>云启卡片</span><span class="zoo-value" data-value="yunqi">0</span>
      </div>
      <label class="zoo-field"><span>后台地址</span><input id="zoo-temu-api-url" value="${loadApiConfig().baseUrl}" placeholder="与 1688 采集器相同"></label>
      <label class="zoo-field"><span>统一个人采集令牌</span><input id="zoo-temu-api-token" type="password" value="${loadApiConfig().apiToken}" placeholder="粘贴 1688 使用的 zoo_... 令牌"></label>
      <label class="zoo-field"><span>搜索关键词（每行一个）</span><textarea id="zoo-temu-keywords" placeholder="dresses for women\nwomen shoes">${loadApiConfig().keywordsInput}</textarea></label>
      <label class="zoo-check"><input id="zoo-temu-same-group" type="checkbox" ${loadApiConfig().sameGroup ? "checked" : ""}><span>所有关键词归类到同一分组</span></label>
      <label class="zoo-field"><span>已有 Temu 分组</span><select id="zoo-temu-group"><option value="">请先保存配置并加载</option></select></label>
      <label class="zoo-field"><span>统一分组名称（同组模式）</span><input id="zoo-temu-group-name" value="${loadApiConfig().groupNameInput}" placeholder="例如：女装趋势"></label>
      <label class="zoo-field">
        <span>最大成功翻页次数</span>
        <input id="zoo-temu-max-loads" type="number" min="0" max="10000" value="${state.maxLoads}">
      </label>
      <div class="zoo-actions">
        <button type="button" class="zoo-save" data-action="save">保存配置并加载分组</button>
        <button type="button" data-action="start">开始采集</button>
        <button type="button" data-action="continue">继续</button>
        <button type="button" class="zoo-stop" data-action="stop">停止</button>
        <button type="button" class="zoo-export" data-action="export">导出 CSV</button>
        <button type="button" class="zoo-clear" data-action="clear">清空状态</button>
      </div>
      <div class="zoo-status" data-value="status">等待开始</div>
    `;
    document.body.appendChild(panel);

    panel
      .querySelector('[data-action="save"]')
      .addEventListener("click", savePanelConfig);
    panel
      .querySelector('[data-action="start"]')
      .addEventListener("click", runCollector);
    panel
      .querySelector('[data-action="continue"]')
      .addEventListener("click", continueCollector);
    panel
      .querySelector('[data-action="stop"]')
      .addEventListener("click", stopCollector);
    panel
      .querySelector('[data-action="export"]')
      .addEventListener("click", exportCsv);
    panel
      .querySelector('[data-action="clear"]')
      .addEventListener("click", clearCollectorState);
    const apiConfig = loadApiConfig();
    if (apiConfig.baseUrl && apiConfig.apiToken) {
      loadBackendGroups(apiConfig.selectedGroupId).catch((error) => {
        state.status = `Temu 分组加载失败：${error.message}`;
        saveState();
      });
    } else {
      state.status = "请填写后台地址和统一个人令牌，然后保存配置并加载分组";
    }
    updatePanel();
  }

  function updatePanel() {
    const panel = document.getElementById(PANEL_ID);
    if (!panel) {
      return;
    }
    const diagnostics = associateProducts();
    const values = {
      records: state.records.length,
      loads: state.successfulLoads,
      page: state.currentSourcePage,
      duplicates: state.duplicateCount,
      shadows: diagnostics.capturedShadowRootCount,
      yunqi: diagnostics.yunqiCardCount,
      status: state.status,
    };
    for (const [name, value] of Object.entries(values)) {
      const element = panel.querySelector(`[data-value="${name}"]`);
      if (element) {
        element.textContent = String(value ?? "");
      }
    }
    panel.querySelector('[data-action="save"]').disabled = state.active;
    panel.querySelector('[data-action="start"]').disabled = state.active;
    panel.querySelector('[data-action="continue"]').disabled =
      state.active || !state.paused;
    panel.querySelector('[data-action="stop"]').disabled = !state.active;
    panel.querySelector('[data-action="export"]').disabled =
      state.records.length === 0;
    panel.querySelector('[data-action="clear"]').disabled = state.active;
  }

  function initialize() {
    createPanel();
    const observer = new MutationObserver(() => {
      if (!document.getElementById(PANEL_ID)) {
        createPanel();
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
    setInterval(updatePanel, 2000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
