import {
  PIXEL_ACTOR_ACTIONS,
  PIXEL_ACTOR_DIRECTIONS,
  PIXEL_ACTOR_MANIFEST,
  PIXEL_ACTOR_ASSETS,
  applyPixelActorLayout,
  pixelActorCellRect,
  pixelActorDisplaySize,
  pixelActorFrame,
  pixelActorGraphPortraitRect,
  pixelActorSpriteConfig,
  queuePixelActorSheets,
  registerPixelActorFrames,
  type PixelActorTexture,
  type PixelActorTextureManager,
} from '../../../core/frontend/src/game/pixelActorAssets'

describe('pixel actor runtime assets', () => {
  it('keeps the five source sheets versioned and declares one shared layout', () => {
    expect(PIXEL_ACTOR_MANIFEST).toHaveLength(6)
    expect(PIXEL_ACTOR_MANIFEST.map((asset) => asset.sourceSize)).toEqual([
      { width: 256, height: 384 },
      { width: 360, height: 360 },
      { width: 256, height: 384 },
      { width: 256, height: 384 },
      { width: 256, height: 384 },
      { width: 360, height: 360 },
    ])
    for (const asset of PIXEL_ACTOR_MANIFEST) {
      expect(asset.url).toContain('/assets/actors/pixel/')
      expect(asset.url).toContain('v1')
      expect(asset.grid).toEqual({ columns: 4, rows: 4 })
      expect(asset.display.originX).toBe(0.5)
      expect(asset.display.originY).toBe(0.95)
    }
    expect(PIXEL_ACTOR_ASSETS.player_001.display).toMatchObject({ width: 44, height: 66 })
    expect(PIXEL_ACTOR_ASSETS.npc_001.display).toMatchObject({ width: 66, height: 66 })
    expect(PIXEL_ACTOR_ASSETS.npc_005.display).toMatchObject({ width: 66, height: 66 })
  })

  it('maps rows to directions and columns to actions', () => {
    expect(PIXEL_ACTOR_DIRECTIONS).toEqual(['down', 'up', 'left', 'right'])
    expect(PIXEL_ACTOR_ACTIONS).toEqual(['idle', 'walkA', 'pass', 'walkB'])
    expect(pixelActorFrame('npc_002', 'up', 'pass')).toMatchObject({
      index: 6,
      row: 1,
      column: 2,
      frameKey: 'npc_002:up:pass',
      rect: { x: 128, y: 96, width: 64, height: 96 },
    })
    expect(pixelActorSpriteConfig('npc_003', 'left', 'walkA')).toEqual({
      textureKey: PIXEL_ACTOR_ASSETS.npc_003.textureKey,
      frame: 'npc_003:left:walkA',
    })
  })

  it('covers odd-sized source sheets without dropping remainder pixels', () => {
    expect(pixelActorCellRect(PIXEL_ACTOR_ASSETS.npc_001, 0, 0)).toEqual({ x: 0, y: 0, width: 90, height: 90 })
    expect(pixelActorCellRect(PIXEL_ACTOR_ASSETS.npc_001, 0, 1)).toEqual({ x: 90, y: 0, width: 90, height: 90 })
    expect(pixelActorCellRect(PIXEL_ACTOR_ASSETS.npc_001, 3, 3)).toEqual({ x: 270, y: 270, width: 90, height: 90 })
  })

  it('centers graph portraits on visible pixels instead of transparent cell padding', () => {
    expect(pixelActorGraphPortraitRect('npc_001')).toEqual({ x: 39, y: 17, width: 37, height: 73 })
    expect(pixelActorGraphPortraitRect('npc_005')).toEqual({ x: 55, y: 20, width: 31, height: 70 })
    expect(pixelActorGraphPortraitRect('player_001')).toEqual({ x: 11, y: 16, width: 39, height: 77 })

    const lin = pixelActorGraphPortraitRect('npc_001')
    const zhou = pixelActorGraphPortraitRect('npc_005')
    expect(lin && lin.x + lin.width / 2).toBeCloseTo(57.5)
    expect(zhou && zhou.x + zhou.width / 2).toBeCloseTo(70.5)
  })

  it('queues each image and registers named frames idempotently', () => {
    const queued: Array<{ key: string; url: string }> = []
    queuePixelActorSheets({ image: (key, url) => queued.push({ key, url }) })
    expect(queued).toHaveLength(6)
    expect(queued[0]).toEqual({ key: PIXEL_ACTOR_ASSETS.player_001.textureKey, url: PIXEL_ACTOR_ASSETS.player_001.url })

    class FakeTexture implements PixelActorTexture {
      readonly source = [{ width: 360, height: 360 }]
      readonly frames = new Set<string>()
      readonly calls: Array<[string, number, number, number, number, number]> = []

      has(name: string): boolean { return this.frames.has(name) }

      add(name: string, sourceIndex: number, x: number, y: number, width: number, height: number): object {
        this.frames.add(name)
        this.calls.push([name, sourceIndex, x, y, width, height])
        return { name }
      }
    }

    const texture = new FakeTexture()
    const textures: PixelActorTextureManager = {
      exists: (key) => key === PIXEL_ACTOR_ASSETS.npc_001.textureKey,
      get: () => texture,
    }
    const first = registerPixelActorFrames(textures, [PIXEL_ACTOR_ASSETS.npc_001])
    expect(first.registered).toHaveLength(16)
    expect(first.existing).toHaveLength(0)
    expect(first.missingTextures).toEqual([])
    expect(first.failed).toEqual([])
    expect(texture.calls[15]).toEqual(['npc_001:right:walkB', 0, 270, 270, 90, 90])

    const second = registerPixelActorFrames(textures, [PIXEL_ACTOR_ASSETS.npc_001])
    expect(second.registered).toHaveLength(0)
    expect(second.existing).toHaveLength(16)
  })

  it('applies the shared bottom-anchored display box', () => {
    const calls: Array<[string, number, number]> = []
    const target = {
      setOrigin: (x: number, y: number) => { calls.push(['origin', x, y]); return target },
      setDisplaySize: (width: number, height: number) => { calls.push(['size', width, height]); return target },
    }
    expect(applyPixelActorLayout(target, 'npc_005')).toBe(target)
    expect(pixelActorDisplaySize('npc_005')).toEqual({ width: 66, height: 66 })
    expect(pixelActorDisplaySize('player_001')).toEqual({ width: 44, height: 66 })
    expect(calls).toEqual([['origin', 0.5, 0.95], ['size', 66, 66]])
  })
})
