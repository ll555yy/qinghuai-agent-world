import { create } from 'zustand'

export type AppPhase = 'intro' | 'agenda' | 'world' | 'ending'
export type SidePanel = 'none' | 'actor' | 'chat' | 'events'

interface ContextMenuState {
  actorId: string
  x: number
  y: number
}

interface UiStore {
  phase: AppPhase
  panel: SidePanel
  selectedActorId: string | null
  selectedConversationId: string | null
  contextMenu: ContextMenuState | null
  setPhase: (phase: AppPhase) => void
  openActor: (actorId: string) => void
  openChat: (conversationId: string) => void
  openEvents: () => void
  closePanel: () => void
  showContextMenu: (menu: ContextMenuState) => void
  closeContextMenu: () => void
}

export const useUiStore = create<UiStore>((set) => ({
  phase: 'intro',
  panel: 'none',
  selectedActorId: null,
  selectedConversationId: null,
  contextMenu: null,
  setPhase: (phase) => set({ phase }),
  openActor: (selectedActorId) => set({ panel: 'actor', selectedActorId, contextMenu: null }),
  openChat: (selectedConversationId) =>
    set({ panel: 'chat', selectedConversationId, contextMenu: null }),
  openEvents: () => set({ panel: 'events', contextMenu: null }),
  closePanel: () => set({ panel: 'none' }),
  showContextMenu: (contextMenu) => set({ contextMenu }),
  closeContextMenu: () => set({ contextMenu: null }),
}))
