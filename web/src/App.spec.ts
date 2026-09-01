import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import App from './App.vue'

describe('App', () => {
  it('renders an honest disconnected placeholder', () => {
    const wrapper = mount(App)

    expect(wrapper.get('h1').text()).toBe('CodingAgentNeo Web')
    expect(wrapper.text()).toContain('尚未连接 Agent 服务')
    expect(wrapper.find('button').exists()).toBe(false)
  })
})
