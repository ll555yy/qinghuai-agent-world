/**
 * Single-pass pixel-art tileset generator for the Qinghuai antique bookstore.
 * 128x64 PNG: 8 columns x 4 rows, 16x16 pixels per tile.
 *
 * Every tile is authored exactly once. Palette rule:
 * warm gold highlights, walnut mid-tones, cool umber/blue-grey shadows.
 *
 * Run: node scripts/generate-tileset.mjs
 */
import { PNG } from 'pngjs'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const TILE = 16
const COLS = 8
const ROWS = 4
const WIDTH = TILE * COLS
const HEIGHT = TILE * ROWS
const png = new PNG({ width: WIDTH, height: HEIGHT })

function px(gx, gy, r, g, b, a = 255) {
  if (gx < 0 || gx >= WIDTH || gy < 0 || gy >= HEIGHT) return
  const offset = (gy * WIDTH + gx) * 4
  if (a >= 255) {
    png.data[offset] = r
    png.data[offset + 1] = g
    png.data[offset + 2] = b
    png.data[offset + 3] = 255
    return
  }
  const sourceAlpha = a / 255
  const targetAlpha = png.data[offset + 3] / 255
  const outputAlpha = sourceAlpha + targetAlpha * (1 - sourceAlpha)
  if (outputAlpha <= 0) return
  png.data[offset] = Math.round((r * sourceAlpha + png.data[offset] * targetAlpha * (1 - sourceAlpha)) / outputAlpha)
  png.data[offset + 1] = Math.round((g * sourceAlpha + png.data[offset + 1] * targetAlpha * (1 - sourceAlpha)) / outputAlpha)
  png.data[offset + 2] = Math.round((b * sourceAlpha + png.data[offset + 2] * targetAlpha * (1 - sourceAlpha)) / outputAlpha)
  png.data[offset + 3] = Math.round(outputAlpha * 255)
}

function fill(tx, ty, x, y, width, height, r, g, b, a = 255) {
  for (let dy = 0; dy < height; dy += 1) {
    for (let dx = 0; dx < width; dx += 1) px(tx * TILE + x + dx, ty * TILE + y + dy, r, g, b, a)
  }
}

function hline(tx, ty, y, x1, x2, r, g, b, a = 255) {
  for (let x = x1; x <= x2; x += 1) px(tx * TILE + x, ty * TILE + y, r, g, b, a)
}

function vline(tx, ty, x, y1, y2, r, g, b, a = 255) {
  for (let y = y1; y <= y2; y += 1) px(tx * TILE + x, ty * TILE + y, r, g, b, a)
}

function ellipse(tx, ty, cx, cy, rx, ry, r, g, b, a = 255) {
  for (let y = -ry; y <= ry; y += 1) {
    for (let x = -rx; x <= rx; x += 1) {
      if ((x * x) / (rx * rx) + (y * y) / (ry * ry) <= 1) px(tx * TILE + cx + x, ty * TILE + cy + y, r, g, b, a)
    }
  }
}

function clearTile(tx, ty) {
  fill(tx, ty, 0, 0, TILE, TILE, 0, 0, 0, 0)
}

function renderStoneFloor(col, base, seam, light, upperJoint, lowerJoint) {
  fill(col, 0, 0, 0, 16, 16, ...base)
  hline(col, 0, 0, 0, 15, ...seam)
  hline(col, 0, 8, 0, 15, ...seam)
  vline(col, 0, upperJoint, 0, 7, ...seam)
  vline(col, 0, lowerJoint, 8, 15, ...seam)
  hline(col, 0, 1, 0, 15, ...light)
  hline(col, 0, 9, 0, 15, light[0] - 6, light[1] - 5, light[2] - 4)
  px(col * TILE + 4, 4, base[0] - 10, base[1] - 7, base[2] - 3)
  px(col * TILE + 5, 4, base[0] - 5, base[1] - 4, base[2] - 2)
  px(col * TILE + 12, 12, base[0] + 7, base[1] + 5, base[2] + 3)
}

function renderWoodFloor(col, base, seam, light, upperJoint, lowerJoint) {
  fill(col, 0, 0, 0, 16, 16, ...base)
  hline(col, 0, 0, 0, 15, ...seam)
  hline(col, 0, 8, 0, 15, ...seam)
  vline(col, 0, upperJoint, 0, 7, ...seam)
  vline(col, 0, lowerJoint, 8, 15, ...seam)
  hline(col, 0, 1, 0, 15, ...light)
  hline(col, 0, 9, 0, 15, light[0] - 5, light[1] - 4, light[2] - 2)
  hline(col, 0, 4, 1, Math.max(2, upperJoint - 2), base[0] - 9, base[1] - 7, base[2] - 4)
  hline(col, 0, 12, lowerJoint + 2, 14, base[0] - 8, base[1] - 6, base[2] - 3)
  px(col * TILE + 2, 5, light[0] - 10, light[1] - 8, light[2] - 5)
  // Extra Stardew-style grain ticks so planks never read as flat colour.
  hline(col, 0, 5, 8, 12, base[0] - 13, base[1] - 10, base[2] - 5)
  hline(col, 0, 13, 2, 5, base[0] - 11, base[1] - 8, base[2] - 4)
  px(col * TILE + 13, 6, base[0] + 13, base[1] + 10, base[2] + 6)
  px(col * TILE + 6, 14, base[0] + 10, base[1] + 8, base[2] + 5)
}

// Row 0: T_EMPTY and floor variants.
// Stardew-style warm palette: honey oak planks in the study, sunlit
// beige stone in the front hall. Bright mid-tones, caramel highlights.
clearTile(0, 0)
renderStoneFloor(1, [188, 170, 140], [138, 120, 96], [212, 196, 166], 8, 0)
renderStoneFloor(2, [172, 154, 126], [124, 108, 86], [196, 178, 148], 4, 12)
renderWoodFloor(3, [200, 148, 86], [156, 106, 52], [216, 168, 104], 11, 4)
renderWoodFloor(4, [184, 134, 74], [142, 96, 46], [204, 154, 92], 6, 13)
renderStoneFloor(5, [196, 178, 146], [144, 126, 100], [218, 202, 170], 11, 3)
renderStoneFloor(6, [180, 166, 142], [130, 118, 98], [204, 190, 164], 6, 14)
renderWoodFloor(7, [210, 160, 98], [162, 112, 58], [226, 178, 114], 13, 5)

// Row 1: architectural surfaces.
fill(0, 1, 0, 0, 16, 16, 49, 29, 19)
hline(0, 1, 1, 0, 15, 104, 68, 41)
hline(0, 1, 3, 0, 15, 76, 48, 30)
hline(0, 1, 14, 0, 15, 31, 18, 13)
hline(0, 1, 15, 0, 15, 20, 12, 9)
for (const x of [3, 11]) {
  fill(0, 1, x, 6, 3, 5, 105, 71, 44)
  hline(0, 1, 6, x, x + 2, 143, 101, 61)
}

fill(1, 1, 0, 0, 16, 16, 149, 136, 116)
hline(1, 1, 0, 0, 15, 179, 163, 137)
hline(1, 1, 14, 0, 15, 116, 102, 88)
hline(1, 1, 15, 0, 15, 91, 78, 68)
for (const [x, y] of [[3, 5], [11, 3], [7, 11], [14, 8]]) px(TILE + x, TILE + y, 133, 120, 103)

fill(2, 1, 0, 0, 16, 16, 67, 42, 27)
vline(2, 1, 0, 0, 15, 35, 22, 16)
vline(2, 1, 2, 0, 15, 91, 60, 38)
vline(2, 1, 13, 0, 15, 107, 73, 47)
vline(2, 1, 15, 0, 15, 42, 27, 19)
for (const y of [4, 11]) {
  hline(2, 1, y, 2, 13, 43, 27, 19)
  hline(2, 1, y + 1, 2, 13, 101, 68, 43)
}

fill(3, 1, 0, 0, 16, 16, 55, 34, 23)
hline(3, 1, 0, 0, 15, 91, 60, 38)
hline(3, 1, 15, 0, 15, 28, 17, 12)
for (const x of [2, 7, 12]) {
  fill(3, 1, x, 3, 3, 9, 137, 126, 108)
  vline(3, 1, x + 1, 3, 11, 74, 48, 30)
}
hline(3, 1, 7, 2, 14, 74, 48, 30)

fill(4, 1, 0, 0, 16, 16, 105, 98, 90)
fill(4, 1, 0, 0, 3, 16, 59, 37, 24)
fill(4, 1, 13, 0, 3, 16, 59, 37, 24)
fill(4, 1, 0, 0, 16, 3, 71, 45, 28)
vline(4, 1, 2, 1, 14, 103, 69, 43)
vline(4, 1, 13, 1, 14, 83, 53, 33)
hline(4, 1, 14, 3, 12, 50, 31, 21)
hline(4, 1, 15, 3, 12, 31, 20, 16)

fill(5, 1, 0, 0, 16, 16, 149, 136, 116)
fill(5, 1, 1, 1, 14, 14, 70, 43, 26)
fill(5, 1, 3, 3, 10, 10, 226, 219, 194)
for (const [x, y] of [[4, 4], [10, 9], [11, 6], [6, 11]]) fill(5, 1, x, y, 2, 2, 111, 134, 102, 100)
for (const x of [2, 6, 10, 14]) vline(5, 1, x, 2, 13, 88, 56, 34)
for (const y of [2, 6, 10, 14]) hline(5, 1, y, 2, 13, 88, 56, 34)
hline(5, 1, 15, 0, 15, 45, 28, 19)

fill(6, 1, 0, 0, 16, 16, 149, 136, 116)
hline(6, 1, 2, 3, 12, 53, 33, 22)
fill(6, 1, 4, 3, 8, 11, 177, 157, 129)
fill(6, 1, 5, 4, 6, 8, 228, 221, 203)
for (const [x, y] of [[7, 5], [6, 7], [8, 8], [9, 9]]) px(6 * TILE + x, TILE + y, 58, 51, 45)
fill(6, 1, 9, 10, 2, 2, 171, 54, 40)
hline(6, 1, 14, 2, 13, 53, 33, 22)

fill(7, 1, 0, 0, 16, 16, 149, 136, 116)
fill(7, 1, 7, 1, 2, 4, 137, 101, 51)
px(7 * TILE + 6, TILE + 2, 181, 142, 72)
hline(7, 1, 4, 3, 12, 145, 105, 56)
fill(7, 1, 4, 5, 8, 8, 74, 47, 30)
fill(7, 1, 5, 6, 6, 6, 226, 167, 68)
fill(7, 1, 6, 7, 4, 4, 255, 230, 143)
for (const x of [4, 11]) vline(7, 1, x, 5, 12, 100, 67, 38)
fill(7, 1, 7, 14, 2, 2, 145, 44, 32)

// Row 2: furniture tiles with 3/4 volume.
clearTile(0, 2)
fill(0, 2, 1, 2, 15, 14, 42, 25, 17)
hline(0, 2, 1, 1, 15, 94, 62, 37)
hline(0, 2, 2, 1, 15, 67, 42, 27)
for (const shelfY of [7, 13]) {
  hline(0, 2, shelfY, 2, 14, 89, 58, 34)
  hline(0, 2, shelfY + 1, 2, 14, 25, 15, 11)
}
const bookColors = [[37, 55, 62], [74, 49, 37], [43, 64, 44], [149, 139, 120], [119, 49, 39], [56, 48, 64]]
for (const [shelfY, start] of [[2, 0], [8, 3]]) {
  let bookX = 2
  let index = start
  while (bookX < 14) {
    const color = bookColors[index % bookColors.length]
    const bookWidth = index % 3 === 0 ? 2 : 1
    fill(0, 2, bookX, shelfY, bookWidth, 5, ...color)
    px(bookX, 2 * TILE + shelfY + 1, color[0] + 25, color[1] + 20, color[2] + 13)
    bookX += bookWidth + 1
    index += 1
  }
}
// Seamless interior edges: coherent end panels are supplied by drawTileObject
// only at the two ends of a collected multi-tile bookshelf footprint.
vline(0, 2, 0, 2, 14, 42, 25, 17)
vline(0, 2, 15, 2, 14, 42, 25, 17)
hline(0, 2, 7, 0, 15, 89, 58, 34)
hline(0, 2, 13, 0, 15, 89, 58, 34)
hline(0, 2, 15, 0, 15, 21, 12, 9)

clearTile(1, 2)
fill(1, 2, 2, 13, 14, 3, 24, 20, 23, 118)
fill(1, 2, 0, 1, 16, 7, 103, 62, 35)
hline(1, 2, 1, 0, 15, 150, 101, 57)
hline(1, 2, 3, 1, 14, 122, 77, 43)
fill(1, 2, 0, 8, 16, 3, 54, 36, 29)
hline(1, 2, 10, 1, 14, 35, 26, 24)
fill(1, 2, 1, 11, 2, 4, 44, 30, 25)
fill(1, 2, 13, 11, 2, 4, 44, 30, 25)
hline(1, 2, 11, 3, 12, 68, 43, 29)

clearTile(2, 2)
ellipse(2, 2, 9, 14, 6, 2, 25, 20, 23, 112)
hline(2, 2, 1, 2, 13, 136, 89, 49)
fill(2, 2, 3, 2, 10, 2, 82, 50, 29)
for (const x of [4, 7, 10, 12]) vline(2, 2, x, 3, 7, 80, 50, 31)
fill(2, 2, 2, 8, 12, 3, 111, 68, 39)
hline(2, 2, 8, 3, 12, 149, 97, 54)
fill(2, 2, 2, 11, 12, 2, 56, 37, 30)
fill(2, 2, 3, 13, 2, 3, 42, 29, 24)
fill(2, 2, 11, 13, 2, 3, 42, 29, 24)

clearTile(3, 2)
ellipse(3, 2, 9, 14, 7, 2, 26, 21, 22, 105)
fill(3, 2, 4, 10, 8, 5, 108, 56, 34)
hline(3, 2, 10, 3, 12, 153, 86, 48)
fill(3, 2, 5, 14, 6, 1, 70, 37, 26)
for (const [x, y, color] of [[2, 5, 0], [5, 2, 1], [8, 1, 2], [10, 3, 1], [12, 6, 0], [6, 6, 2]]) {
  const greens = [[43, 68, 34], [62, 91, 45], [89, 126, 61]]
  const leaf = greens[color]
  fill(3, 2, x, y, 3, 4, ...leaf)
}

clearTile(4, 2)
ellipse(4, 2, 9, 14, 6, 2, 26, 20, 20, 110)
fill(4, 2, 5, 12, 6, 2, 70, 44, 28)
fill(4, 2, 4, 4, 8, 8, 81, 51, 31)
fill(4, 2, 5, 5, 6, 6, 226, 178, 76)
fill(4, 2, 6, 6, 4, 4, 255, 231, 147)
for (const x of [4, 11]) vline(4, 2, x, 4, 11, 103, 67, 38)
hline(4, 2, 4, 4, 11, 142, 98, 53)

clearTile(5, 2)
ellipse(5, 2, 9, 15, 8, 2, 22, 17, 19, 118)
fill(5, 2, 0, 3, 16, 13, 50, 29, 18)
fill(5, 2, 0, 0, 16, 5, 108, 69, 39)
hline(5, 2, 0, 0, 15, 150, 103, 58)
for (const x of [1, 8]) {
  fill(5, 2, x, 6, 6, 6, 39, 23, 15)
  hline(5, 2, 6, x, x + 5, 81, 50, 28)
  px(5 * TILE + x + 3, 2 * TILE + 9, 188, 149, 73)
}
hline(5, 2, 15, 0, 15, 22, 12, 9)

clearTile(6, 2)
fill(6, 2, 0, 0, 16, 16, 92, 47, 41)
fill(6, 2, 1, 1, 14, 14, 111, 58, 48)
for (const edge of [1, 14]) {
  hline(6, 2, edge, 1, 14, 175, 133, 73)
  vline(6, 2, edge, 1, 14, 175, 133, 73)
}
fill(6, 2, 5, 5, 6, 6, 128, 70, 53)
for (const [x, y] of [[6, 6], [9, 6], [6, 9], [9, 9]]) px(6 * TILE + x, 2 * TILE + y, 188, 147, 84)

clearTile(7, 2)
ellipse(7, 2, 9, 15, 8, 2, 23, 18, 20, 115)
fill(7, 2, 0, 2, 16, 12, 91, 54, 31)
hline(7, 2, 2, 0, 15, 144, 93, 51)
fill(7, 2, 2, 5, 12, 7, 222, 212, 192)
fill(7, 2, 1, 4, 2, 9, 72, 44, 28)
fill(7, 2, 13, 4, 2, 9, 72, 44, 28)
fill(7, 2, 4, 5, 7, 1, 168, 126, 64)
hline(7, 2, 8, 4, 9, 58, 52, 48)
fill(7, 2, 10, 9, 4, 3, 32, 32, 36)

// Row 3: transparent decorative props.
clearTile(0, 3)
ellipse(0, 3, 9, 15, 7, 2, 24, 20, 21, 105)
fill(0, 3, 3, 11, 10, 5, 115, 61, 36)
hline(0, 3, 11, 2, 13, 160, 91, 50)
for (const [x, y, color] of [[1, 4, 0], [4, 1, 1], [8, 0, 2], [11, 2, 1], [13, 5, 0], [6, 5, 2]]) {
  const greens = [[42, 70, 35], [63, 96, 47], [91, 133, 62]]
  fill(0, 3, x, y, 3, 7, ...greens[color])
}

clearTile(1, 3)
for (let y = 0; y < 3; y += 1) {
  px(TILE + 7, 3 * TILE + y, 93, 78, 61)
  px(TILE + 8, 3 * TILE + y, 93, 78, 61)
}
fill(1, 3, 4, 3, 8, 9, 78, 50, 31)
fill(1, 3, 5, 4, 6, 7, 232, 180, 76)
fill(1, 3, 6, 5, 4, 5, 255, 232, 146)
hline(1, 3, 3, 3, 12, 151, 108, 58)
fill(1, 3, 6, 12, 4, 3, 158, 48, 36)

clearTile(2, 3)
fill(2, 3, 5, 1, 6, 2, 108, 73, 39)
fill(2, 3, 3, 3, 10, 10, 66, 42, 27)
fill(2, 3, 4, 2, 8, 12, 151, 111, 56)
fill(2, 3, 5, 4, 6, 7, 217, 203, 173)
for (const [x, y] of [[7, 4], [10, 7], [7, 10], [5, 7]]) px(2 * TILE + x, 3 * TILE + y, 71, 54, 39)
vline(2, 3, 7, 5, 7, 46, 40, 36)
hline(2, 3, 7, 7, 9, 46, 40, 36)
fill(2, 3, 6, 12, 4, 2, 84, 54, 31)

clearTile(3, 3)
// Deep tea-brown curled cover/shadow separates the pale paper from any desk.
fill(3, 3, 3, 6, 12, 8, 45, 29, 21, 155)
fill(3, 3, 2, 5, 12, 8, 74, 50, 34)
fill(3, 3, 2, 4, 6, 8, 234, 226, 207)
fill(3, 3, 8, 4, 6, 8, 238, 230, 213)
vline(3, 3, 7, 4, 11, 157, 77, 48)
for (const y of [6, 8, 10]) {
  hline(3, 3, y, 3, 6, 78, 68, 57)
  hline(3, 3, y, 9, 12, 78, 68, 57)
}

clearTile(4, 3)
fill(4, 3, 1, 10, 14, 5, 112, 75, 43)
hline(4, 3, 10, 1, 14, 157, 113, 66)
for (const x of [4, 8, 12]) vline(4, 3, x, 11, 14, 81, 53, 33)
fill(4, 3, 3, 5, 6, 5, 132, 62, 41)
fill(4, 3, 4, 4, 4, 2, 157, 77, 48)
fill(4, 3, 8, 6, 3, 2, 132, 62, 41)
for (const x of [10, 13]) {
  fill(4, 3, x, 7, 3, 3, 157, 187, 171)
  hline(4, 3, 7, x, x + 2, 210, 221, 202)
}

clearTile(5, 3)
fill(5, 3, 3, 5, 12, 10, 74, 50, 34, 165)
fill(5, 3, 2, 4, 12, 10, 232, 224, 207)
vline(5, 3, 2, 5, 13, 121, 91, 62)
hline(5, 3, 13, 3, 13, 103, 73, 49)
fill(5, 3, 2, 10, 4, 4, 35, 34, 36)
fill(5, 3, 3, 11, 2, 2, 65, 63, 65)
vline(5, 3, 12, 2, 9, 142, 100, 53)
px(5 * TILE + 12, 3 * TILE + 10, 39, 35, 33)
fill(5, 3, 9, 12, 6, 2, 103, 68, 39)
for (const y of [6, 8]) hline(5, 3, y, 6, 9, 67, 59, 51)

clearTile(6, 3)
ellipse(6, 3, 8, 12, 6, 3, 111, 84, 55)
ellipse(6, 3, 8, 9, 6, 5, 158, 129, 82)
ellipse(6, 3, 8, 9, 4, 3, 190, 158, 102)
ellipse(6, 3, 8, 9, 2, 1, 113, 89, 57)
hline(6, 3, 13, 3, 12, 77, 57, 43)

clearTile(7, 3)
for (let x = 2; x <= 13; x += 1) {
  const distance = Math.abs(x - 7.5)
  const top = Math.round(5 + distance * 0.7)
  vline(7, 3, x, top, 12, 229, 215, 181)
}
for (let x = 3; x < 13; x += 2) vline(7, 3, x, 7, 13, 153, 116, 63)
fill(7, 3, 7, 12, 2, 4, 106, 71, 40)
px(7 * TILE + 8, 3 * TILE + 8, 163, 56, 43)

// Regression guard: transparent tile must stay untouched.
clearTile(0, 0)
let emptyAlpha = 0
for (let y = 0; y < TILE; y += 1) {
  for (let x = 0; x < TILE; x += 1) emptyAlpha += png.data[(y * WIDTH + x) * 4 + 3]
}
if (emptyAlpha !== 0) throw new Error('T_EMPTY contains non-transparent pixels')

const outputDirectory = join(scriptDir, '..', 'public', 'assets', 'scenes')
mkdirSync(outputDirectory, { recursive: true })
const outputPath = join(outputDirectory, 'tileset.png')
writeFileSync(outputPath, PNG.sync.write(png))
console.log('Tileset generated: ' + outputPath)
console.log('T_EMPTY alpha sum: ' + emptyAlpha + '; tiles: ' + (COLS * ROWS))
