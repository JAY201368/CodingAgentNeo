import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ApprovalDialog from './ApprovalDialog.vue'

const approval = {
  requestId: 'correlation-approval',
  correlationId: 'correlation-approval',
  toolName: 'read_file',
  argumentsSummary: 'backend-redacted summary',
  timeoutSeconds: 30,
} as const

describe('ApprovalDialog', () => {
  it('emits at most one exact decision and locks both buttons after the first click', async () => {
    const wrapper = mount(ApprovalDialog, { props: { approval } })
    const buttons = wrapper.findAll('button')
    await buttons[0].trigger('click')
    await buttons[1].trigger('click')

    expect(wrapper.emitted('decide')).toEqual([['correlation-approval', true]])
    expect((buttons[0].element as HTMLButtonElement).disabled).toBe(true)
    expect((buttons[1].element as HTMLButtonElement).disabled).toBe(true)
  })

  it('emits a single exact rejection without exposing command contents', async () => {
    const wrapper = mount(ApprovalDialog, { props: { approval } })
    await wrapper.get('.approval-dialog__deny').trigger('click')
    expect(wrapper.emitted('decide')).toEqual([['correlation-approval', false]])
    expect(wrapper.text()).not.toContain('command')
  })

  it('treats Escape and a disconnected stream as fail-closed non-decisions', async () => {
    const wrapper = mount(ApprovalDialog, {
      props: { approval, streamAvailable: false },
    })
    await wrapper.get('[role="dialog"]').trigger('keydown.esc')
    expect(wrapper.emitted('decide')).toBeUndefined()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('未因 Escape')

    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('decide')).toBeUndefined()
  })

  it('does not emit for an empty request ID', async () => {
    const wrapper = mount(ApprovalDialog, {
      props: {
        approval: { ...approval, requestId: '   ' },
      },
    })
    await wrapper.findAll('button')[0].trigger('click')
    expect(wrapper.emitted('decide')).toBeUndefined()
  })
})
