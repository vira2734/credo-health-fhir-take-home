<script setup>
import { onMounted, ref } from 'vue'

import {
  fetchPatient,
  fetchPatients,
  formatEffective,
  formatObservationValue,
} from './api.js'

const patients = ref([])
const selectedPatient = ref(null)
const listLoading = ref(true)
const detailLoading = ref(false)
const errorMessage = ref('')

onMounted(async () => {
  try {
    patients.value = await fetchPatients()
  } catch {
    errorMessage.value = 'Unable to load patients.'
  } finally {
    listLoading.value = false
  }
})

async function selectPatient(patientId) {
  detailLoading.value = true
  errorMessage.value = ''
  try {
    selectedPatient.value = await fetchPatient(patientId)
  } catch {
    errorMessage.value = 'Unable to load patient details.'
  } finally {
    detailLoading.value = false
  }
}
</script>

<template>
  <header class="app-header">
    <div>
      <p class="eyebrow">Credo Health take-home</p>
      <h1>FHIR Migration Viewer</h1>
      <p class="subtitle">Synthetic Patient and Observation data migrated from HAPI FHIR R4.</p>
    </div>
  </header>

  <main class="layout">
    <aside class="panel patient-panel" aria-labelledby="patient-heading">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">Migrated records</p>
          <h2 id="patient-heading">Patients</h2>
        </div>
        <span class="count">{{ patients.length }}</span>
      </div>

      <p v-if="listLoading" class="state">Loading patients…</p>
      <p v-else-if="patients.length === 0" class="state">No Patients have been migrated yet.</p>
      <ul v-else class="patient-list">
        <li v-for="patient in patients" :key="patient.id">
          <button
            type="button"
            class="patient-button"
            :class="{ selected: selectedPatient?.id === patient.id }"
            :aria-pressed="selectedPatient?.id === patient.id"
            @click="selectPatient(patient.id)"
          >
            <strong>{{ patient.display_name || patient.fhir_id }}</strong>
            <span>{{ patient.fhir_id }}</span>
          </button>
        </li>
      </ul>
    </aside>

    <section class="panel detail-panel" aria-live="polite">
      <p v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</p>
      <p v-if="detailLoading" class="state">Loading patient details…</p>
      <div v-else-if="selectedPatient">
        <div class="patient-summary">
          <div>
            <p class="eyebrow">Patient</p>
            <h2>{{ selectedPatient.display_name || selectedPatient.fhir_id }}</h2>
          </div>
          <dl>
            <div>
              <dt>FHIR ID</dt>
              <dd>{{ selectedPatient.fhir_id }}</dd>
            </div>
            <div>
              <dt>Gender</dt>
              <dd>{{ selectedPatient.gender || 'Not recorded' }}</dd>
            </div>
            <div>
              <dt>Birth date</dt>
              <dd>{{ selectedPatient.birth_date || 'Not recorded' }}</dd>
            </div>
          </dl>
        </div>

        <div class="observation-heading">
          <h3>Observations</h3>
          <span class="count">{{ selectedPatient.observations.length }}</span>
        </div>
        <p v-if="selectedPatient.observations.length === 0" class="state">No Observations were found for this Patient.</p>
        <ul v-else class="observation-list">
          <li v-for="observation in selectedPatient.observations" :key="observation.id">
            <div>
              <strong>{{ observation.display_label || observation.code_text || observation.fhir_id }}</strong>
              <span>{{ formatEffective(observation) }}</span>
            </div>
            <p class="result">{{ formatObservationValue(observation) }}</p>
            <span class="status">{{ observation.status || 'Status unknown' }}</span>
          </li>
        </ul>
      </div>
      <div v-else class="empty-detail">
        <p class="eyebrow">Patient details</p>
        <h2>Select a Patient</h2>
        <p>Choose a migrated Patient to inspect associated Observations.</p>
      </div>
    </section>
  </main>
</template>
