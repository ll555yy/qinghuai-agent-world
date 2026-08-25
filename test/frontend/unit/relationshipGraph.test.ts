import { buildRelationshipGraph } from '../../../core/frontend/src/graph/relationshipGraph'
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
    expect(graph.nodes.every((node) => Object.keys(node).sort().join(',') === 'color,id,kind,label,nodeValue,role')).toBe(true)
    expect(JSON.stringify(graph)).not.toContain('隐藏目标')
    expect(JSON.stringify(graph)).not.toContain('秘密关系')
  })
})
