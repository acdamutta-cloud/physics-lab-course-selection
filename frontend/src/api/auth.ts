import { api } from './client'

export interface UserProfile {
  id: string
  login_name: string
  user_type: 'STUDENT' | 'TEACHER' | 'ADMIN'
  name: string | null
  student_no: string | null
  enrollment_year: number | null
  major_name: string | null
  employee_no: string | null
  department: string | null
  title: string | null
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: UserProfile
}

export async function loginApi(login_name: string, password: string): Promise<TokenResponse> {
  return api.post<TokenResponse>('/auth/login', { login_name, password })
}
