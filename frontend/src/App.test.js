import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

function jsonResponse(payload) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('App', () => {
  it('lists Patients and shows generic Observation values after selection', async () => {
    global.fetch = vi.fn()
      .mockReturnValueOnce(jsonResponse([
        { id: 1, fhir_id: 'patient-1', display_name: 'Synthetic Alpha' },
      ]))
      .mockReturnValueOnce(jsonResponse({
        id: 1,
        fhir_id: 'patient-1',
        display_name: 'Synthetic Alpha',
        gender: 'unknown',
        birth_date: '1980-04',
        observations: [
          {
            id: 1,
            fhir_id: 'observation-1',
            status: 'final',
            display_label: 'Body temperature',
            value_type: 'valueQuantity',
            value: { value: 98.6, unit: 'degrees F' },
            effective: '2024-03',
          },
          {
            id: 2,
            fhir_id: 'observation-2',
            status: 'final',
            display_label: 'Synthetic boolean result',
            value_type: 'valueBoolean',
            value: false,
          },
        ],
      }))

    const wrapper = mount(App)
    await flushPromises()
    await wrapper.get('.patient-button').trigger('click')
    await flushPromises()

    expect(global.fetch).toHaveBeenNthCalledWith(1, '/api/patients/', expect.any(Object))
    expect(global.fetch).toHaveBeenNthCalledWith(2, '/api/patients/1/', expect.any(Object))
    expect(wrapper.text()).toContain('Body temperature')
    expect(wrapper.text()).toContain('98.6 degrees F')
    expect(wrapper.text()).toContain('Synthetic boolean result')
    expect(wrapper.text()).toContain('No')
  })

  it('shows a safe error when the Patient list cannot load', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('synthetic-sensitive-marker'))

    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('Unable to load patients.')
    expect(wrapper.text()).not.toContain('synthetic-sensitive-marker')
  })
})
