// Marcin Kowalik <mkowalik@agh.edu.pl>
// SPDX-License-Identifier: GPL-3.0-only
/** One decode in progress and one replaceable latest frame, never a FIFO. */
export class InventureLatestPainter {
  private active: HTMLImageElement | null = null
  private pending: string | null = null
  private requested: string | null = null
  private painted: HTMLImageElement | null = null
  private disposed = false

  constructor (private readonly draw: (image: HTMLImageElement) => void) {}

  submit (uri: string) {
    if (this.disposed || uri === this.requested) return
    this.requested = this.pending = uri
    this.next()
  }

  redraw () {
    if (!this.disposed && this.painted) this.draw(this.painted)
  }

  clear () {
    this.pending = this.requested = null
    this.painted = null
    const image = this.active
    this.active = null
    if (image) {
      image.onload = image.onerror = null
      image.src = ''
    }
  }

  dispose () {
    this.disposed = true
    this.clear()
  }

  private next () {
    if (this.disposed || this.active || this.pending === null) return
    const uri = this.pending
    this.pending = null
    const image = this.active = new Image()
    const finish = (ok: boolean) => {
      if (this.disposed || this.active !== image) return
      this.active = null
      image.onload = image.onerror = null
      // Never repaint a selection superseded while this PNG was decoding.
      if (ok && this.requested === uri && image.naturalWidth === 256 && image.naturalHeight === 64) {
        this.painted = image
        try { this.draw(image) } catch { /* A view failure cannot stop event reception. */ }
      } else if (this.requested === uri) {
        this.requested = null // Permit the same image to be retried after failure.
      }
      if (this.pending === uri) this.pending = null
      this.next()
    }
    image.onload = () => finish(true)
    image.onerror = () => finish(false)
    image.src = uri
  }
}
