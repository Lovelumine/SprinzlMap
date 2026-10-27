import type { Ref } from "vue";
import { apiFetch } from "@/shared/api";
import type { AlignmentResult, SessionListItem } from "../model/types";
import { getErrorMessage, readErrorMessage } from "../model/utils";

interface SessionCreateResponse {
  request_id?: string;
  email?: string;
  expires_at?: string;
}

interface SessionGetResponse {
  request_id?: string;
  email?: string;
  expires_at?: string;
  results?: AlignmentResult[];
  payload?: Record<string, unknown>;
}

interface SessionListResponse {
  items?: SessionListItem[];
}

interface UseSprinzlSessionDeps {
  requestEmail: Ref<string>;
  requestId: Ref<string>;
  requestExpiresAt: Ref<string>;
  requestMessage: Ref<string>;
  requestError: Ref<string>;
  boundEmail: Ref<string>;
  emailBinding: Ref<boolean>;
  queryId: Ref<string>;
  queryEmail: Ref<string>;
  emailHistory: Ref<SessionListItem[]>;
  results: Ref<AlignmentResult[]>;
  interpretationLanguage: Ref<"en" | "zh-CN" | "de" | "fr">;
}

export function useSprinzlSession(deps: UseSprinzlSessionDeps) {
  const {
    requestEmail,
    requestId,
    requestExpiresAt,
    requestMessage,
    requestError,
    boundEmail,
    emailBinding,
    queryId,
    queryEmail,
    emailHistory,
    results,
    interpretationLanguage,
  } = deps;

  function normalizedValidEmail(value: string): string {
    const email = value.trim().toLowerCase();
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? email : "";
  }

  function resetRequestState() {
    requestMessage.value = "";
    requestError.value = "";
    requestId.value = "";
    requestExpiresAt.value = "";
    boundEmail.value = "";
    emailBinding.value = false;
  }

  async function saveSession(
    payload: Record<string, unknown>,
    savedResults: AlignmentResult[],
  ) {
    requestError.value = "";
    requestMessage.value = "";
    const enteredEmail = requestEmail.value.trim();
    const emailForCreate = normalizedValidEmail(enteredEmail);
    try {
      const res = await apiFetch("/align/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          payload,
          results: savedResults,
          email: emailForCreate || undefined,
        }),
      });
      if (!res.ok) throw new Error(await readErrorMessage(res));
      const data = (await res.json()) as SessionCreateResponse;
      requestId.value = data.request_id || "";
      boundEmail.value = data.email || emailForCreate;
      if (boundEmail.value) requestEmail.value = boundEmail.value;
      requestExpiresAt.value = data.expires_at || "";
      if (requestId.value) {
        queryId.value = requestId.value;
      }
      requestMessage.value = requestId.value
        ? `Request ID generated: ${requestId.value}`
        : "Saved, but no request ID returned.";
      if (enteredEmail && !emailForCreate) {
        requestError.value = "The run was saved, but the email format is invalid. Correct it below to associate this Request ID.";
      }
      if (requestId.value && requestEmail.value.trim()) {
        const exists = emailHistory.value.some(
          (item) => item.request_id === requestId.value,
        );
        if (!exists) {
          emailHistory.value = [
            { request_id: requestId.value, expires_at: requestExpiresAt.value },
            ...emailHistory.value,
          ];
        }
      }
    } catch (err: unknown) {
      requestError.value = getErrorMessage(err, "Failed to save request ID.");
    }
  }

  async function bindEmailToSession() {
    const rid = requestId.value.trim();
    const email = normalizedValidEmail(requestEmail.value);
    requestError.value = "";
    requestMessage.value = "";
    if (!rid) {
      requestError.value = "No Request ID is available to update.";
      return;
    }
    if (!email) {
      requestError.value = "Please enter a valid email.";
      return;
    }
    emailBinding.value = true;
    const wasBound = Boolean(boundEmail.value);
    try {
      const res = await apiFetch(
        `/align/session/${encodeURIComponent(rid)}/email`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        },
      );
      if (!res.ok) throw new Error(await readErrorMessage(res));
      const data = (await res.json()) as SessionCreateResponse;
      boundEmail.value = data.email || email;
      requestEmail.value = boundEmail.value;
      queryEmail.value = boundEmail.value;
      requestMessage.value = wasBound
        ? "Email updated for this Request ID."
        : "Email saved for this Request ID.";
    } catch (err: unknown) {
      requestError.value = getErrorMessage(err, "Failed to save email.");
    } finally {
      emailBinding.value = false;
    }
  }

  function copyRequestId() {
    const rid = requestId.value;
    if (!rid) return;
    navigator.clipboard
      ?.writeText(rid)
      .then(() => {
        requestMessage.value = "Request ID copied.";
      })
      .catch(() => {
        requestError.value = "Copy failed. Please copy manually.";
      });
  }

  function useRequestId() {
    if (!requestId.value) return;
    queryId.value = requestId.value;
    requestMessage.value = "Request ID ready to query.";
  }

  async function loadByRequestId(explicitId?: unknown) {
    const isEventLike =
      explicitId &&
      typeof explicitId === "object" &&
      "target" in (explicitId as object);
    const raw =
      typeof explicitId === "string"
        ? explicitId
        : explicitId == null || isEventLike
          ? queryId.value
          : String(explicitId);
    const rid = String(raw || "").trim();
    requestError.value = "";
    requestMessage.value = "";
    if (!rid) {
      requestError.value = "Please enter a request ID.";
      return;
    }
    requestMessage.value = "Loading request ID...";
    try {
      const res = await apiFetch(`/align/session/${encodeURIComponent(rid)}`);
      if (!res.ok) throw new Error(await readErrorMessage(res));
      const data = (await res.json()) as SessionGetResponse;
      requestId.value = data.request_id || rid;
      boundEmail.value = data.email || "";
      requestEmail.value = boundEmail.value;
      requestExpiresAt.value = data.expires_at || "";
      results.value = Array.isArray(data.results) ? data.results : [];
      const savedLanguage = String(data.payload?.interpretation_language || "");
      if (["en", "zh-CN", "de", "fr"].includes(savedLanguage)) {
        interpretationLanguage.value = savedLanguage as "en" | "zh-CN" | "de" | "fr";
      }
      requestMessage.value = results.value.length
        ? "Results loaded."
        : "Request loaded, but no results found.";
      queryId.value = rid;
    } catch (err: unknown) {
      requestError.value = getErrorMessage(err, "Failed to load request ID.");
    }
  }

  async function loadByEmail() {
    const email = queryEmail.value.trim();
    requestError.value = "";
    requestMessage.value = "";
    emailHistory.value = [];
    if (!email) {
      requestError.value = "Please enter an email.";
      return;
    }
    requestMessage.value = "Loading email history...";
    try {
      const res = await apiFetch(
        `/align/session?email=${encodeURIComponent(email)}`,
      );
      if (!res.ok) throw new Error(await readErrorMessage(res));
      const data = (await res.json()) as SessionListResponse;
      emailHistory.value = Array.isArray(data.items) ? data.items : [];
      if (!emailHistory.value.length) {
        requestMessage.value = "No request IDs found for this email.";
      }
    } catch (err: unknown) {
      requestError.value = getErrorMessage(
        err,
        "Failed to load history by email.",
      );
    }
  }

  return {
    resetRequestState,
    saveSession,
    bindEmailToSession,
    copyRequestId,
    useRequestId,
    loadByRequestId,
    loadByEmail,
  };
}
