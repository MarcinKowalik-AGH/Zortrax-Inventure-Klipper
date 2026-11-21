// Marcin Kowalik <mkowalik@agh.edu.pl>
// @vitest-environment jsdom
import Vue from 'vue'
import VueRouter from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { navigateInventureSidebar } from './inventure-sidebar'

Vue.use(VueRouter)
const view = Vue.extend({ render: h => h('div') })
function fixture (loader: () => Promise<typeof view>) {
  return new VueRouter({
    mode: 'abstract',
    routes: [
      { path: '/', name: 'home', component: view },
      { path: '/console', name: 'console', component: loader },
      { path: '/settings', name: 'settings', component: view }
    ]
  })
}

describe('Inventure left sidebar', () => {
  it.each(['Failed to fetch dynamically imported module: Console.js', 'Importing a module script failed.', 'error loading dynamically imported module: Console.js', 'Unable to preload CSS for Settings.css'])('recovers a failed route import: %s', async message => {
    const router = fixture(() => Promise.reject(new TypeError(message)))
    router.onError(() => {})
    const reload = vi.fn()
    await router.push('/')
    await navigateInventureSidebar(router, 'console', reload)
    expect(reload).toHaveBeenCalledExactlyOnceWith('/console')
  })

  it('opens normal pages and respects duplicate, cancelled and superseded navigation', async () => {
    let failRequest!: (error: Error) => void
    const router = fixture(() => new Promise((_resolve, reject) => { failRequest = reject }))
    router.onError(() => {})
    const reload = vi.fn()
    await router.push('/')
    const first = navigateInventureSidebar(router, 'console', reload)
    await navigateInventureSidebar(router, 'settings', reload)
    failRequest(new TypeError('Importing a module script failed.'))
    await first
    expect(router.currentRoute.name).toBe('settings')
    await navigateInventureSidebar(router, 'settings', reload)
    router.beforeEach((_to, _from, next) => next(false))
    await navigateInventureSidebar(router, 'home', reload)
    expect(router.currentRoute.name).toBe('settings')
    expect(reload).not.toHaveBeenCalled()
  })
})
