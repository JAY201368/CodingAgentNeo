import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import ApprovalDialog from './ApprovalDialog.vue'

const approval = {
  requestId: 'correlation-approval',
  correlationId: 'correlation-approval',
  toolName: 'bash',
  argumentsSummary: '"python3 seal.py"',
  timeoutSeconds: 30,
} as const

describe('ApprovalDialog', () => {
  it('moves focus into the dialog, traps Tab, and restores the opener', async () => {
    const opener = document.createElement('button')
    opener.type = 'button'
    document.body.append(opener)
    opener.focus()

    const wrapper = mount(ApprovalDialog, { props: { approval }, attachTo: document.body })
    await nextTick()
    await nextTick()
    expect(document.activeElement).toBe(wrapper.get('.approval-dialog__actions .primary-action').element)

    const dialog = wrapper.get('[role="dialog"]')
    const buttons = wrapper.findAll('[role="dialog"] button')
    expect(buttons).toHaveLength(2)
    ;(buttons[1].element as HTMLButtonElement).focus()
    await dialog.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(buttons[0].element)

    ;(buttons[0].element as HTMLButtonElement).focus()
    await dialog.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(buttons[1].element)

    opener.focus()
    await nextTick()
    expect(document.activeElement).toBe(buttons[0].element)

    await wrapper.setProps({ approval: null })
    await nextTick()
    expect(document.activeElement).toBe(opener)
    wrapper.unmount()
    opener.remove()
  })

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

  it('keeps the dialog open on Escape and treats a disconnected stream as a non-decision', async () => {
    const wrapper = mount(ApprovalDialog, {
      props: { approval, streamAvailable: false },
    })
    await wrapper.get('[role="dialog"]').trigger('keydown.esc')
    expect(wrapper.emitted('decide')).toBeUndefined()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('稍后处理')
    expect(wrapper.text()).not.toContain('打开授权对话框')
    expect(wrapper.findAll('[role="dialog"] button')).toHaveLength(2)

    await wrapper.get('.primary-action').trigger('click')
    await wrapper.get('.approval-dialog__deny').trigger('click')
    expect(wrapper.emitted('decide')).toBeUndefined()
  })

  it('shows only the command and approve/reject actions', async () => {
    const wrapper = mount(ApprovalDialog, { props: { approval } })
    const text = wrapper.text()
    expect(text).toContain('需要授权')
    expect(text).toContain('bash')
    expect(text).toContain('"python3 seal.py"')
    expect(text).toContain('批准')
    expect(text).toContain('拒绝')
    expect(text).not.toContain('仅此一个待处理请求')
    expect(text).not.toContain('脱敏摘要')
    expect(text).not.toContain('请求 ID')
    expect(text).not.toContain('稍后处理')
    expect(wrapper.find('.approval-collapsed').exists()).toBe(false)
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
