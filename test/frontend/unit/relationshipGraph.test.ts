import {
  buildRelationshipGraph,
  filterRelationshipGraph,
  RELATIONSHIP_LAYOUT,
} from '../../../core/frontend/src/graph/relationshipGraph'
import { snapshot } from './fixtures'

describe('buildRelationshipGraph', () => {
  it('keeps every public actor visible but creates no edges for an empty conversation', () => {
    const graph = buildRelationshipGraph(snapshot({
      conversations: [{
        conversationId: 'empty-chat',
        creationSeq: 1,
        participants: [],
        status: 'closed',
      }],
    }))

    expect(graph.nodes.map((node) => node.id)).toEqual(['player_001', 'npc_001', 'npc_002'])
    expect(graph.links).toEqual([])
  })

  it('deduplicates repeated participants before making pairwise links', () => {
    const graph = buildRelationshipGraph(snapshot({
      conversations: [{
        conversationId: 'repeated-participants',
        creationSeq: 1,
        participants: ['player_001', 'npc_001', 'npc_002', 'npc_002', 'player_001'],
        status: 'closed',
      }],
    }))

    expect(graph.links).toEqual([
      {
        source: 'player_001',
        target: 'npc_001',
        conversationCount: 1,
        active: false,
        label: '1 次共同聊天',
      },
      {
        source: 'player_001',
        target: 'npc_002',
        conversationCount: 1,
        active: false,
        label: '1 次共同聊天',
      },
      {
        source: 'npc_001',
        target: 'npc_002',
        conversationCount: 1,
        active: false,
        label: '1 次共同聊天',
      },
    ])
    expect(graph.nodes.find((node) => node.id === 'player_001')?.nodeValue).toBe(10)
    expect(graph.nodes.find((node) => node.id === 'npc_001')?.nodeValue).toBe(8)
    expect(graph.nodes.find((node) => node.id === 'npc_002')?.nodeValue).toBe(8)
  })

  it('creates one edge for each pair in a multi-person conversation', () => {
    const graph = buildRelationshipGraph(snapshot({
      conversations: [{
        conversationId: 'group-chat',
        creationSeq: 1,
        participants: ['npc_001', 'player_001', 'npc_002'],
        status: 'open',
      }],
    }))

    expect(graph.links).toHaveLength(3)
    expect(new Set(graph.links.map(({ source, target }) => `${source}:${target}`))).toEqual(new Set([
      'npc_001:player_001',
      'npc_001:npc_002',
      'player_001:npc_002',
    ]))
    expect(graph.links.every((link) => link.conversationCount === 1 && link.active)).toBe(true)
    expect(graph.links.every((link) => link.source !== link.target)).toBe(true)
  })

  it('counts the same pair across sessions and keeps open/closed state per aggregated edge', () => {
    const graph = buildRelationshipGraph(snapshot({
      conversations: [
        {
          conversationId: 'closed-chat',
          creationSeq: 1,
          participants: ['player_001', 'npc_001'],
          status: 'closed',
        },
        {
          conversationId: 'open-chat',
          creationSeq: 2,
          participants: ['npc_001', 'player_001'],
          status: 'open',
        },
        {
          conversationId: 'closed-group',
          creationSeq: 3,
          participants: ['npc_001', 'npc_002'],
          status: 'closed',
        },
      ],
    }))

    expect(graph.links).toEqual([
      {
        source: 'player_001',
        target: 'npc_001',
        conversationCount: 2,
        active: true,
        label: '2 次共同聊天 · 正在进行',
      },
      {
        source: 'npc_001',
        target: 'npc_002',
        conversationCount: 1,
        active: false,
        label: '1 次共同聊天',
      },
    ])
  })

  it('keeps a closed relationship from participant history after actors leave', () => {
    const graph = buildRelationshipGraph(snapshot({
      conversations: [{
        conversationId: 'closed-after-leave',
        creationSeq: 1,
        participants: ['player_001'],
        participantHistory: ['player_001', 'npc_002'],
        status: 'closed',
      }],
    }))

    expect(graph.links).toEqual([{
      source: 'player_001',
      target: 'npc_002',
      conversationCount: 1,
      active: false,
      label: '1 次共同聊天',
    }])
  })

  it('does not mark a historical participant as active after they leave an open chat', () => {
    const graph = buildRelationshipGraph(snapshot({
      conversations: [{
        conversationId: 'still-open',
        creationSeq: 1,
        participants: ['npc_001', 'npc_002'],
        participantHistory: ['player_001', 'npc_001', 'npc_002'],
        status: 'open',
      }],
    }))

    const playerLink = graph.links.find((link) => (
      link.source === 'player_001' || link.target === 'player_001'
    ))
    const npcLink = graph.links.find((link) => (
      new Set([link.source, link.target]).has('npc_001') &&
      new Set([link.source, link.target]).has('npc_002')
    ))
    expect(playerLink?.active).toBe(false)
    expect(npcLink?.active).toBe(true)
  })

  it('does not mutate the input snapshot while deduplicating and aggregating', () => {
    const input = snapshot({
      conversations: [{
        conversationId: 'repeated-participants',
        creationSeq: 1,
        participants: ['player_001', 'npc_001', 'npc_001'],
        status: 'open',
      },
      {
        conversationId: 'closed-chat',
        creationSeq: 2,
        participants: ['npc_001', 'player_001'],
        status: 'closed',
      }],
    })
    const before = structuredClone(input)

    buildRelationshipGraph(input)

    expect(input).toEqual(before)
  })

  it('assigns deterministic grouped initial coordinates with the player at the center', () => {
    const input = snapshot()
    const first = buildRelationshipGraph(input)
    const reordered = buildRelationshipGraph({
      ...input,
      actors: [...input.actors].reverse(),
    })

    expect(first.nodes).toEqual(reordered.nodes)
    expect(first.nodes[0]).toMatchObject({
      id: 'player_001',
      group: 'player',
      fx: RELATIONSHIP_LAYOUT.player.fx,
      fy: RELATIONSHIP_LAYOUT.player.fy,
    })

    const npcNodes = first.nodes.filter((node) => node.group === 'npc')
    expect(npcNodes).toHaveLength(2)
    for (const node of npcNodes) {
      expect(Math.hypot(node.fx, node.fy)).toBeCloseTo(RELATIONSHIP_LAYOUT.npcRingRadius, 8)
    }
    expect(new Set(npcNodes.map((node) => `${node.fx}:${node.fy}`)).size).toBe(npcNodes.length)
  })

  it('keeps five angular slots stable when a later snapshot adds an NPC', () => {
    const initial = buildRelationshipGraph(snapshot())
    const expanded = buildRelationshipGraph(snapshot({
      actors: [
        ...snapshot().actors,
        {
          actorId: 'npc_003',
          kind: 'npc',
          name: '顾砚秋',
          role: '社区志愿者',
          publicBackground: '常在巷口帮忙。',
          publicImpression: ['热心'],
        },
      ],
    }))

    for (const actorId of ['npc_001', 'npc_002']) {
      const initialNode = initial.nodes.find((node) => node.id === actorId)
      const expandedNode = expanded.nodes.find((node) => node.id === actorId)
      expect(initialNode).toBeDefined()
      expect(expandedNode).toEqual(initialNode)
    }
    expect(expanded.nodes.find((node) => node.id === 'npc_003')).toMatchObject({ group: 'npc' })
  })

  it('filters to direct player relationships without mutating the complete graph', () => {
    const graph = buildRelationshipGraph(snapshot({
      conversations: [
        {
          conversationId: 'player-chat',
          creationSeq: 1,
          participants: ['player_001', 'npc_001'],
          status: 'closed',
        },
        {
          conversationId: 'npc-chat',
          creationSeq: 2,
          participants: ['npc_001', 'npc_002'],
          status: 'open',
        },
      ],
    }))
    const before = structuredClone(graph)

    const playerGraph = filterRelationshipGraph(graph, 'player')

    expect(playerGraph.nodes.map((node) => node.id)).toEqual(['player_001', 'npc_001'])
    expect(playerGraph.links).toEqual([{
      source: 'player_001',
      target: 'npc_001',
      conversationCount: 1,
      active: false,
      label: '1 次共同聊天',
    }])
    expect(graph).toEqual(before)
  })

  it('defaults the pure filter to all and returns independent arrays', () => {
    const graph = buildRelationshipGraph(snapshot({
      conversations: [{
        conversationId: 'player-chat',
        creationSeq: 1,
        participants: ['player_001', 'npc_001'],
        status: 'open',
      }],
    }))

    const all = filterRelationshipGraph(graph)

    expect(all).toEqual(graph)
    expect(all).not.toBe(graph)
    expect(all.nodes).not.toBe(graph.nodes)
    expect(all.links).not.toBe(graph.links)
  })

  it('exposes only public node fields and observed co-participation edges', () => {
    const publicSnapshot = snapshot({
      conversations: [],
      currentWorldState: {
        privateRelationships: [{
          source: 'npc_001',
          target: 'npc_002',
          label: '秘密关系，不应成为公开连线',
        }],
        hiddenTrust: { npc_001: { npc_002: 99 } },
      },
    })
    publicSnapshot.actors[1] = Object.assign({}, publicSnapshot.actors[1], {
      coreSecrets: ['隐藏目标'],
      trust: 2,
    })

    const graph = buildRelationshipGraph(publicSnapshot)

    expect(graph.links).toEqual([])
    expect(graph.nodes.every((node) => Object.keys(node).sort().join(',') === 'color,fx,fy,group,id,kind,label,nodeValue,role')).toBe(true)
    expect(JSON.stringify(graph)).not.toContain('隐藏目标')
    expect(JSON.stringify(graph)).not.toContain('秘密关系')
  })
})
