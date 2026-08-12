export interface AlbumTile {
  x: number
  y: number
  width: number
  height: number
}

export interface AlbumLayout {
  width: number
  height: number
  tiles: AlbumTile[]
}

const GAP = 2
const MIN_TILE = 96

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function finish(width: number, tiles: AlbumTile[]): AlbumLayout {
  return {
    width,
    height: Math.max(...tiles.map((tile) => tile.y + tile.height)),
    tiles,
  }
}

function two(ratios: number[], width: number): AlbumLayout {
  const [first, second] = ratios
  const average = (first + second) / 2
  if (first > 1.2 && second > 1.2 && average > 1.4 && Math.abs(first - second) < 0.2) {
    const height = Math.round(Math.min(width / first, width / second, (width - GAP) / 2))
    return finish(width, [
      { x: 0, y: 0, width, height },
      { x: 0, y: height + GAP, width, height },
    ])
  }

  if ((first > 1.2 && second > 1.2) || (first >= 0.8 && first <= 1.2 && second >= 0.8 && second <= 1.2)) {
    const itemWidth = (width - GAP) / 2
    const height = Math.round(Math.min(itemWidth / first, itemWidth / second, width))
    return finish(width, [
      { x: 0, y: 0, width: itemWidth, height },
      { x: itemWidth + GAP, y: 0, width: itemWidth, height },
    ])
  }

  const secondWidth = Math.min(
    Math.round(Math.max(0.4 * (width - GAP), (width - GAP) / first / (1 / first + 1 / second))),
    width - GAP - Math.round(MIN_TILE * 1.5),
  )
  const firstWidth = width - secondWidth - GAP
  const height = Math.min(width, Math.round(Math.min(firstWidth / first, secondWidth / second)))
  return finish(width, [
    { x: 0, y: 0, width: firstWidth, height },
    { x: firstWidth + GAP, y: 0, width: secondWidth, height },
  ])
}

function three(ratios: number[], width: number): AlbumLayout {
  if (ratios[0] < 0.8) {
    const fullHeight = width
    const lowerHeight = Math.round(Math.min((fullHeight - GAP) / 2, ratios[1] * (width - GAP) / (ratios[1] + ratios[2])))
    const upperHeight = fullHeight - lowerHeight - GAP
    const rightWidth = Math.max(MIN_TILE, Math.round(Math.min((width - GAP) / 2, lowerHeight * ratios[2], upperHeight * ratios[1])))
    const leftWidth = Math.min(Math.round(fullHeight * ratios[0]), width - GAP - rightWidth)
    return finish(width, [
      { x: 0, y: 0, width: leftWidth, height: fullHeight },
      { x: leftWidth + GAP, y: 0, width: rightWidth, height: upperHeight },
      { x: leftWidth + GAP, y: upperHeight + GAP, width: rightWidth, height: lowerHeight },
    ])
  }

  const topHeight = Math.round(Math.min(width / ratios[0], 0.66 * (width - GAP)))
  const lowerWidth = (width - GAP) / 2
  const lowerHeight = Math.min(width - topHeight - GAP, Math.round(Math.min(lowerWidth / ratios[1], lowerWidth / ratios[2])))
  return finish(width, [
    { x: 0, y: 0, width, height: topHeight },
    { x: 0, y: topHeight + GAP, width: lowerWidth, height: lowerHeight },
    { x: lowerWidth + GAP, y: topHeight + GAP, width: width - lowerWidth - GAP, height: lowerHeight },
  ])
}

function four(ratios: number[], width: number): AlbumLayout {
  if (ratios[0] > 1.2) {
    const topHeight = Math.round(Math.min(width / ratios[0], 0.66 * (width - GAP)))
    const rowHeight = Math.round((width - 2 * GAP) / (ratios[1] + ratios[2] + ratios[3]))
    const firstWidth = Math.max(MIN_TILE, Math.round(Math.min(0.4 * (width - 2 * GAP), rowHeight * ratios[1])))
    const lastWidth = Math.round(Math.max(MIN_TILE, 0.33 * (width - 2 * GAP), rowHeight * ratios[3]))
    const middleWidth = width - firstWidth - lastWidth - 2 * GAP
    const lowerHeight = Math.min(width - topHeight - GAP, rowHeight)
    return finish(width, [
      { x: 0, y: 0, width, height: topHeight },
      { x: 0, y: topHeight + GAP, width: firstWidth, height: lowerHeight },
      { x: firstWidth + GAP, y: topHeight + GAP, width: middleWidth, height: lowerHeight },
      { x: firstWidth + middleWidth + 2 * GAP, y: topHeight + GAP, width: lastWidth, height: lowerHeight },
    ])
  }

  const fullHeight = width
  const leftWidth = Math.round(Math.min(fullHeight * ratios[0], 0.6 * (width - GAP)))
  const rightWidth = Math.max(MIN_TILE, Math.min(width - leftWidth - GAP, Math.round((fullHeight - 2 * GAP) / (1 / ratios[1] + 1 / ratios[2] + 1 / ratios[3]))))
  const firstHeight = Math.round(rightWidth / ratios[1])
  const secondHeight = Math.round(rightWidth / ratios[2])
  return finish(width, [
    { x: 0, y: 0, width: leftWidth, height: fullHeight },
    { x: leftWidth + GAP, y: 0, width: rightWidth, height: firstHeight },
    { x: leftWidth + GAP, y: firstHeight + GAP, width: rightWidth, height: secondHeight },
    { x: leftWidth + GAP, y: firstHeight + secondHeight + 2 * GAP, width: rightWidth, height: fullHeight - firstHeight - secondHeight - 2 * GAP },
  ])
}

function collectLineCounts(remaining: number, limits: number[], rows: number[], result: number[][]) {
  if (!limits.length) {
    if (!remaining) result.push([...rows])
    return
  }
  const [limit, ...rest] = limits
  const maxRest = rest.reduce((sum, value) => sum + value, 0)
  const minimum = Math.max(1, remaining - maxRest)
  const maximum = Math.min(limit, remaining - rest.length)
  for (let size = minimum; size <= maximum; size += 1) {
    rows.push(size)
    collectLineCounts(remaining - size, rest, rows, result)
    rows.pop()
  }
}

function partitions(count: number, average: number) {
  const result: number[][] = []
  for (const limits of [
    [3, 3],
    [3, average < 0.85 ? 4 : 3, 3],
    [3, 3, 3, 4],
  ]) {
    collectLineCounts(count, limits, [], result)
  }
  if (result.length) return result

  const rowCount = Math.max(5, Math.ceil((count - 4) / 3) + 1)
  const rows = Array.from({ length: rowCount }, () => 1)
  let remaining = count - rowCount
  for (let index = rowCount - 1; index >= 0 && remaining; index -= 1) {
    const limit = index === rowCount - 1 ? 4 : 3
    const added = Math.min(remaining, limit - 1)
    rows[index] += added
    remaining -= added
  }
  return remaining ? [] : [rows]
}

function many(sourceRatios: number[], width: number): AlbumLayout {
  const average = sourceRatios.reduce((sum, ratio) => sum + ratio, 0) / sourceRatios.length
  const ratios = sourceRatios.map((ratio) => average > 1.1 ? clamp(ratio, 1, 2.75) : clamp(ratio, 0.6667, 1))
  let best: { rows: number[]; heights: number[]; score: number } | undefined

  for (const rows of partitions(ratios.length, average)) {
    const heights: number[] = []
    let offset = 0
    for (const count of rows) {
      const sum = ratios.slice(offset, offset + count).reduce((total, ratio) => total + ratio, 0)
      heights.push((width - (count - 1) * GAP) / sum)
      offset += count
    }
    const totalHeight = heights.reduce((sum, height) => sum + height, 0) + (rows.length - 1) * GAP
    const narrowPenalty = Math.min(...heights) < MIN_TILE ? 1.5 : 1
    const orderPenalty = rows.some((count, index) => index > 0 && rows[index - 1] > count) ? 1.5 : 1
    const score = Math.abs(totalHeight - width) * narrowPenalty * orderPenalty
    if (!best || score < best.score) best = { rows, heights, score }
  }

  const tiles: AlbumTile[] = []
  let index = 0
  let y = 0
  for (let row = 0; row < best!.rows.length; row += 1) {
    const count = best!.rows[row]
    const height = Math.round(best!.heights[row])
    let x = 0
    for (let column = 0; column < count; column += 1) {
      const itemWidth = column === count - 1 ? width - x : Math.round(ratios[index] * best!.heights[row])
      tiles.push({ x, y, width: itemWidth, height })
      x += itemWidth + GAP
      index += 1
    }
    y += height + GAP
  }
  return finish(width, tiles)
}

export function calculateAlbumLayout(sourceRatios: number[], maxWidth: number): AlbumLayout {
  const width = Math.round(maxWidth)
  const ratios = sourceRatios.map((ratio) => Number.isFinite(ratio) && ratio > 0 ? ratio : 1)
  if (ratios.length === 1) {
    const height = Math.round(Math.min(width / ratios[0], width))
    return finish(width, [{ x: 0, y: 0, width, height }])
  }
  if (ratios.length === 2) return two(ratios, width)
  if (ratios.length === 3) return three(ratios, width)
  if (ratios.length === 4) return four(ratios, width)
  return many(ratios, width)
}
