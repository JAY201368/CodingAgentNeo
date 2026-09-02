import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TaskComposer from './TaskComposer.vue'

describe('TaskComposer permissions', () => {
  it('expands upward on demand, emits a mode change, and collapses after selection', async () => {
    const wrapper = mount(TaskComposer, { props: { approvalMode: 'ask' } })
    const trigger = wrapper.get('.composer__permission-trigger')
    expect(trigger.text()).toContain('询问')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)

    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    const options = wrapper.findAll('[role="option"]')
    expect(options).toHaveLength(3)
    expect(options[0].attributes('aria-selected')).toBe('true')

    await options[1].trigger('click')
    expect(wrapper.emitted('permissionChange')).toEqual([['auto']])
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
  })

  it('locks the collapsed permission control while a change is pending', () => {
    const wrapper = mount(TaskComposer, { props: { permissionsUpdating: true } })
    expect((wrapper.get('.composer__permission-trigger').element as HTMLButtonElement).disabled)
      .toBe(true)
  })
})
