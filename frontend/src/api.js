async function getJson(path) {
  const response = await fetch(path, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`)
  }
  return response.json()
}

export function fetchPatients() {
  return getJson('/api/patients/')
}

export function fetchPatient(patientId) {
  return getJson(`/api/patients/${patientId}/`)
}

export function formatObservationValue(observation) {
  const value = observation.value
  if (observation.value_type === 'valueQuantity' && value) {
    const number = value.value ?? observation.value_numeric
    const unit = value.unit || observation.value_unit
    return [value.comparator, number, unit].filter((part) => part !== null && part !== undefined && part !== '').join(' ')
  }
  if (observation.value_type === 'valueBoolean') {
    return value ? 'Yes' : 'No'
  }
  if (observation.value_type === 'valueCodeableConcept' && value) {
    return value.text || value.coding?.[0]?.display || value.coding?.[0]?.code || 'Coded result'
  }
  if (typeof value === 'string' || typeof value === 'number') {
    return String(value)
  }
  if (value !== null && value !== undefined) {
    return JSON.stringify(value)
  }
  return observation.data_absent_reason?.text || 'No result recorded'
}

export function formatEffective(observation) {
  if (observation.effective_at) {
    return new Date(observation.effective_at).toLocaleString()
  }
  if (typeof observation.effective === 'string') {
    return observation.effective
  }
  return observation.effective ? JSON.stringify(observation.effective) : 'Time not recorded'
}
