"use strict";

(() => {
  const history = document.querySelector("#history");
  const form = document.querySelector("#chat-form");
  const input = document.querySelector("#message");
  const send = document.querySelector("#send");
  const newChat = document.querySelector("#new-chat");
  const activity = document.querySelector("#activity");
  const operations = {
    add_book: "Dodanie książki do katalogu",
    borrow_book: "Wypożyczenie książki",
    return_book: "Zwrot książki",
    delete_book: "Usunięcie książki z katalogu",
  };
  const parameters = {
    serial_number: "Numer seryjny",
    borrower_card_number: "Numer karty",
    title: "Tytuł",
    author: "Autor",
  };
  let threadId = null;
  let busy = false;
  let pending = null;
  let needsReset = false;
  const sentDecisions = new Set();

  class RequestError extends Error {
    constructor(message, status = 0) {
      super(message);
      this.status = status;
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function scrollToLatest() {
    history.scrollTop = history.scrollHeight;
  }

  function message(role, text) {
    const item = element("article", `message message-${role}`);
    const labels = { user: "Ty", assistant: "Asystent", error: "Błąd" };
    item.append(element("p", "message-label", labels[role]));
    item.append(element("p", "message-text", text));
    if (role === "error") item.setAttribute("role", "alert");
    history.append(item);
    scrollToLatest();
    return item;
  }

  function updateControls() {
    input.disabled = busy || Boolean(pending) || needsReset;
    send.disabled = input.disabled || !input.value.trim();
    newChat.disabled = busy;
    activity.classList.toggle("is-loading", busy);
    activity.textContent = busy
      ? "Przetwarzanie…"
      : needsReset
        ? "Rozpocznij nową rozmowę, aby kontynuować."
        : pending ? "Sprawdź operacje w historii rozmowy i wybierz decyzję." : "";
    if (pending) {
      pending.approve.disabled = busy || pending.sent || !pending.ready;
      pending.cancel.disabled = busy || pending.sent;
      pending.retry.disabled = busy || pending.sent;
    }
  }

  async function request(path, body, timeout = 210000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: body === undefined ? { Accept: "application/json" } : {
          Accept: "application/json", "Content-Type": "application/json",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        credentials: "same-origin",
        cache: "no-store",
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : null;
        const fallback = response.status === 422
          ? "Niepoprawne dane. Wiadomość musi mieć od 1 do 4000 znaków."
          : `Serwer nie może obsłużyć żądania (HTTP ${response.status}).`;
        throw new RequestError(detail || fallback, response.status);
      }
      if (!data) throw new RequestError("Serwer zwrócił niepoprawną odpowiedź.");
      return data;
    } catch (error) {
      if (error instanceof RequestError) throw error;
      throw new RequestError(error.name === "AbortError"
        ? "Przekroczono czas oczekiwania na odpowiedź."
        : "Nie udało się połączyć z serwerem. Sprawdź połączenie z siecią.");
    } finally {
      clearTimeout(timer);
    }
  }

  function validateReply(data) {
    if (typeof data.thread_id !== "string" || !data.thread_id
      || typeof data.message !== "string"
      || !["completed", "requires_confirmation"].includes(data.status)
      || (threadId && data.thread_id !== threadId)) {
      throw new RequestError("Serwer zwrócił niepoprawną odpowiedź czatu.");
    }
    if (data.status === "requires_confirmation") {
      const confirmation = data.pending_confirmation;
      if (!confirmation || typeof confirmation.confirmation_id !== "string"
        || !confirmation.confirmation_id || !Array.isArray(confirmation.operations)
        || !confirmation.operations.length
        || sentDecisions.has(`${data.thread_id}:${confirmation.confirmation_id}`)
        || confirmation.operations.some(operation => {
          if (!operation || !Object.hasOwn(operations, operation.name)
            || !operation.args || typeof operation.args !== "object"
            || Array.isArray(operation.args)) return true;
          const args = operation.args;
          return typeof args.serial_number !== "string"
            || !/^[0-9]{6}$/.test(args.serial_number)
            || (operation.name === "add_book"
              && (typeof args.title !== "string" || !args.title.trim()
                || typeof args.author !== "string" || !args.author.trim()))
            || (operation.name === "borrow_book"
              && (typeof args.borrower_card_number !== "string"
                || !/^[0-9]{6}$/.test(args.borrower_card_number)));
        })) {
        throw new RequestError("Nie można odczytać szczegółów operacji do potwierdzenia.");
      }
    }
  }

  function addParameter(list, name, value) {
    list.append(element("dt", "", name));
    list.append(element("dd", "", typeof value === "string" ? value : JSON.stringify(value)));
  }

  async function loadTitles(card) {
    card.ready = false;
    card.retry.hidden = true;
    card.status.textContent = "Pobieranie tytułów książek…";
    updateControls();
    try {
      // The pending Chat API contract carries serial numbers. Resolve titles
      // through the existing REST API; never infer the book from model prose.
      await Promise.all(card.titles.map(async ({ operation, title }) => {
        const book = await request(
          `/api/v1/books/${encodeURIComponent(operation.args.serial_number)}`,
          undefined, 10000,
        );
        if (book.serial_number !== operation.args.serial_number
          || typeof book.title !== "string" || !book.title.trim()) {
          throw new RequestError("Niepoprawne dane książki.");
        }
        title.textContent = book.title;
      }));
      card.ready = true;
      card.status.textContent = "Decyzja dotyczy wszystkich operacji z tej listy.";
    } catch {
      card.status.textContent = "Nie udało się odczytać tytułu książki. Ponów odczyt lub anuluj operację. Potwierdzenie jest zablokowane.";
      card.retry.hidden = false;
    }
    updateControls();
    scrollToLatest();
  }

  async function showConfirmation(parent, confirmation) {
    const panel = element("section", "confirmation");
    panel.setAttribute("aria-label", "Potwierdzenie operacji bibliotecznych");
    panel.append(element("h2", "", "Sprawdź przed zatwierdzeniem"));
    panel.append(element("p", "", "Poniższe zmiany zostaną wykonane dopiero po kliknięciu „Potwierdź”."));
    const titles = [];
    for (const [index, operation] of confirmation.operations.entries()) {
      const item = element("section", "operation");
      item.append(element("h3", "", `${index + 1}. ${operations[operation.name]}`));
      const list = element("dl");
      if (operation.name !== "add_book") {
        addParameter(list, "Tytuł", "Pobieranie…");
        titles.push({ operation, title: list.lastElementChild });
      }
      for (const [key, value] of Object.entries(operation.args)) {
        addParameter(list, Object.hasOwn(parameters, key) ? parameters[key] : key, value);
      }
      item.append(list);
      panel.append(item);
    }
    const status = element("p", "decision-status");
    status.setAttribute("role", "status");
    const actions = element("div", "confirmation-actions");
    const approve = element("button", "button primary", "Potwierdź");
    const cancel = element("button", "button secondary", "Anuluj");
    const retry = element("button", "button secondary", "Ponów odczyt");
    for (const button of [approve, cancel, retry]) button.type = "button";
    retry.hidden = true;
    actions.append(approve, cancel, retry);
    panel.append(status, actions);
    parent.append(panel);
    const card = {
      id: confirmation.confirmation_id, titles, approve, cancel, retry, status,
      ready: false, sent: false,
    };
    pending = card;
    approve.addEventListener("click", () => decide(card, true));
    cancel.addEventListener("click", () => decide(card, false));
    retry.addEventListener("click", async () => {
      if (busy || pending !== card || card.sent) return;
      busy = true;
      await loadTitles(card);
      busy = false;
      updateControls();
    });
    await loadTitles(card);
  }

  async function receive(data) {
    validateReply(data);
    threadId = data.thread_id;
    const item = message("assistant", data.message);
    if (data.status === "requires_confirmation") {
      await showConfirmation(item, data.pending_confirmation);
    }
  }

  function reportError(error, decision = false) {
    // Never replay a write after an uncertain transport outcome. The backend
    // may have committed it even if its final response did not reach us.
    needsReset = decision || ![403, 422].includes(error.status);
    let text = error.message;
    if (needsReset) {
      text += " Rozpocznij nową rozmowę.";
      if (decision || pending) text += " Przed ponowieniem operacji sprawdź stan książki w katalogu.";
    }
    message("error", text);
  }

  async function decide(card, approved) {
    const key = `${threadId}:${card.id}`;
    if (busy || pending !== card || card.sent || sentDecisions.has(key)
      || (approved && !card.ready)) return;
    card.sent = true;
    sentDecisions.add(key);
    busy = true;
    updateControls();
    card.status.textContent = approved ? "Wysyłanie zgody…" : "Wysyłanie anulowania…";
    try {
      const data = await request("/api/v1/chat/confirm", {
        thread_id: threadId, confirmation_id: card.id, approved,
      });
      card.status.textContent = approved ? "Wysłano decyzję: potwierdź." : "Wysłano decyzję: anuluj.";
      pending = null;
      await receive(data);
    } catch (error) {
      card.status.textContent = "Nie udało się odebrać wyniku. Decyzja nie zostanie wysłana ponownie.";
      pending = null;
      reportError(error, true);
    } finally {
      busy = false;
      updateControls();
      focusNext();
    }
  }

  function focusNext() {
    if (needsReset) newChat.focus();
    else if (pending) pending.cancel.focus();
    else input.focus();
    scrollToLatest();
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const text = input.value.trim();
    if (busy || pending || needsReset || !text) return;
    message("user", text);
    input.value = "";
    busy = true;
    updateControls();
    try {
      await receive(await request("/api/v1/chat", { thread_id: threadId, message: text }));
    } catch (error) {
      reportError(error);
    } finally {
      busy = false;
      updateControls();
      focusNext();
    }
  });

  input.addEventListener("input", updateControls);
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!send.disabled) form.requestSubmit();
    }
  });

  function reset() {
    if (busy) return;
    threadId = null;
    pending = null;
    needsReset = false;
    sentDecisions.clear();
    history.replaceChildren();
    input.value = "";
    message("assistant", "Cześć! Pomogę Ci znaleźć książkę, sprawdzić jej dostępność lub obsłużyć wypożyczenie. Od czego zaczynamy?");
    updateControls();
  }

  newChat.addEventListener("click", () => { reset(); input.focus(); });
  reset();
})();
