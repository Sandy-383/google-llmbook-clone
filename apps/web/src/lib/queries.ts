"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import {
  api,
  ApiError,
  type ChatMessage,
  type Note,
  type Notebook,
  type Source,
  type SourceContent,
  type StudioKind,
  type User,
} from "./api";

export const queryKeys = {
  me: ["me"] as const,
  notebooks: ["notebooks"] as const,
  notebook: (id: string) => ["notebooks", id] as const,
  sources: (notebookId: string) => ["notebooks", notebookId, "sources"] as const,
  messages: (notebookId: string) => ["notebooks", notebookId, "messages"] as const,
  notes: (notebookId: string) => ["notebooks", notebookId, "notes"] as const,
};

const isPending = (s: Source) => s.status === "queued" || s.status === "processing";

/** Current user; sends the browser to /login when the session is missing or expired. */
export function useRequireUser() {
  const router = useRouter();
  const me = useQuery({ queryKey: queryKeys.me, queryFn: () => api<User>("/auth/me") });
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;
  useEffect(() => {
    if (unauthenticated) router.replace("/login");
  }, [unauthenticated, router]);
  return me;
}

/** Whether sign-up is open, needs an invite code, or is closed. */
export function useAuthConfig(enabled: boolean) {
  return useQuery({
    queryKey: ["auth-config"],
    queryFn: () => api<{ registration: "open" | "invite" | "closed" }>("/auth/config"),
    enabled,
    staleTime: Infinity,
  });
}

export function useAuth(mode: "login" | "register") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      email: string;
      password: string;
      display_name?: string | null;
      invite_code?: string;
    }) =>
      api<User>(`/auth/${mode}`, { method: "POST", json: body }),
    onSuccess: (user) => {
      client.clear();
      client.setQueryData(queryKeys.me, user);
    },
  });
}

export function useLogout() {
  const client = useQueryClient();
  const router = useRouter();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST" }),
    onSettled: () => {
      client.clear();
      router.replace("/login");
    },
  });
}

export function useNotebooks(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.notebooks,
    queryFn: () => api<Notebook[]>("/notebooks"),
    enabled,
  });
}

export function useNotebook(id: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.notebook(id),
    queryFn: () => api<Notebook>(`/notebooks/${id}`),
    enabled,
  });
}

export function useCreateNotebook() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (title: string) => api<Notebook>("/notebooks", { method: "POST", json: { title } }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.notebooks }),
  });
}

export function useRenameNotebook() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api<Notebook>(`/notebooks/${id}`, { method: "PATCH", json: { title } }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.notebooks }),
  });
}

/** Sources of a notebook; polls while any is being processed, and more slowly while a source
 * guide is being written (that takes a minute or more). */
export function useSources(notebookId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.sources(notebookId),
    queryFn: () => api<Source[]>(`/notebooks/${notebookId}/sources`),
    enabled,
    refetchInterval: (query) => {
      const sources = query.state.data ?? [];
      if (sources.some(isPending)) return 1500;
      if (sources.some((s) => s.guide_status === "pending")) return 4000;
      return false;
    },
  });
}

export function useRegenerateGuide(notebookId: string) {
  return useSourceMutation(notebookId, (sourceId: string) =>
    api<Source>(`/notebooks/${notebookId}/sources/${sourceId}/guide`, { method: "POST" }),
  );
}

/** Notes, newest first; polls while a studio note is being written. */
export function useNotes(notebookId: string) {
  return useQuery({
    queryKey: queryKeys.notes(notebookId),
    queryFn: () => api<Note[]>(`/notebooks/${notebookId}/notes`),
    refetchInterval: (query) =>
      query.state.data?.some((n) => n.status === "generating") ? 3000 : false,
  });
}

function useNoteMutation<TVariables, TResult>(
  notebookId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.notes(notebookId) }),
  });
}

export function useCreateNote(notebookId: string) {
  return useNoteMutation(notebookId, (body: { title: string; content: string }) =>
    api<Note>(`/notebooks/${notebookId}/notes`, { method: "POST", json: body }),
  );
}

export function useSaveAnswer(notebookId: string) {
  return useNoteMutation(notebookId, (messageId: string) =>
    api<Note>(`/notebooks/${notebookId}/notes/from-message`, {
      method: "POST",
      json: { message_id: messageId },
    }),
  );
}

export function useUpdateNote(notebookId: string) {
  return useNoteMutation(
    notebookId,
    ({ id, ...body }: { id: string; title?: string; content?: string }) =>
      api<Note>(`/notebooks/${notebookId}/notes/${id}`, { method: "PATCH", json: body }),
  );
}

export function useDeleteNote(notebookId: string) {
  return useNoteMutation(notebookId, (noteId: string) =>
    api<void>(`/notebooks/${notebookId}/notes/${noteId}`, { method: "DELETE" }),
  );
}

export function useGenerateStudio(notebookId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { kind: StudioKind; source_ids: string[] }) =>
      api<Note>(`/notebooks/${notebookId}/studio`, { method: "POST", json: body }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.notes(notebookId) });
      // Sources from before guides existed get their guide requested too.
      void client.invalidateQueries({ queryKey: queryKeys.sources(notebookId) });
    },
  });
}

function useSourceMutation<TVariables>(
  notebookId: string,
  mutationFn: (variables: TVariables) => Promise<unknown>,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.sources(notebookId) }),
  });
}

export function useUploadSource(notebookId: string) {
  return useSourceMutation(notebookId, (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api<Source>(`/notebooks/${notebookId}/sources`, { method: "POST", body: form });
  });
}

export function useAddTextSource(notebookId: string) {
  return useSourceMutation(notebookId, (body: { title: string; text: string }) =>
    api<Source>(`/notebooks/${notebookId}/sources/text`, { method: "POST", json: body }),
  );
}

export function useDeleteSource(notebookId: string) {
  return useSourceMutation(notebookId, (sourceId: string) =>
    api<void>(`/notebooks/${notebookId}/sources/${sourceId}`, { method: "DELETE" }),
  );
}

export function useRetrySource(notebookId: string) {
  return useSourceMutation(notebookId, (sourceId: string) =>
    api<Source>(`/notebooks/${notebookId}/sources/${sourceId}/retry`, { method: "POST" }),
  );
}

export function useSourceContent(notebookId: string, sourceId: string) {
  return useQuery({
    queryKey: ["notebooks", notebookId, "sources", sourceId, "content"],
    queryFn: () => api<SourceContent>(`/notebooks/${notebookId}/sources/${sourceId}/content`),
    staleTime: Infinity, // a processed source never changes
  });
}

export function useMessages(notebookId: string) {
  return useQuery({
    queryKey: queryKeys.messages(notebookId),
    queryFn: () => api<ChatMessage[]>(`/notebooks/${notebookId}/messages`),
  });
}

export function useClearMessages(notebookId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>(`/notebooks/${notebookId}/messages`, { method: "DELETE" }),
    onSuccess: () => client.setQueryData(queryKeys.messages(notebookId), []),
  });
}

export function useDeleteNotebook() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/notebooks/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.notebooks }),
  });
}
