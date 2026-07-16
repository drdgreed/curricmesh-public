import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AuthProvider } from "./auth/AuthContext";
import { SessionLanguageProvider } from "./i18n/SessionLanguageContext";
import { RequireAuth } from "./auth/RequireAuth";
import { Layout } from "./components/Layout";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { Graph } from "./pages/Graph";
import { Course } from "./pages/Course";
import { Changes } from "./pages/Changes";
import { Diff } from "./pages/Diff";
import { AIInbox } from "./pages/AIInbox";
import { MonitorQueue } from "./pages/MonitorQueue";
import { Analytics } from "./pages/Analytics";
import { NewCCR } from "./pages/NewCCR";
import { AuthorChange } from "./pages/AuthorChange";
import { Review } from "./pages/Review";
import { CourseBuilder } from "./pages/builder/CourseBuilder";
import { MyCourses } from "./pages/learn/MyCourses";
import { Catalog } from "./pages/learn/Catalog";
import { Player } from "./pages/learn/Player";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <SessionLanguageProvider>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route
                path="/"
                element={
                  <RequireAuth>
                    <Layout>
                      <Dashboard />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/history"
                element={
                  <RequireAuth>
                    <Layout>
                      <History />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/graph"
                element={
                  <RequireAuth>
                    <Layout>
                      <Graph />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/course"
                element={
                  <RequireAuth>
                    <Layout>
                      <Course />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/changes"
                element={
                  <RequireAuth>
                    <Layout>
                      <Changes />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/analytics"
                element={
                  <RequireAuth>
                    <Layout>
                      <Analytics />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/propose"
                element={
                  <RequireAuth>
                    <Layout>
                      <AuthorChange />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/builder"
                element={
                  <RequireAuth>
                    <Layout>
                      <CourseBuilder />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/review"
                element={
                  <RequireAuth>
                    <Layout>
                      <Review />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/ccrs/new"
                element={
                  <RequireAuth>
                    <Layout>
                      <NewCCR />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/ai-inbox"
                element={
                  <RequireAuth>
                    <Layout>
                      <AIInbox />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/monitor-queue"
                element={
                  <RequireAuth>
                    <Layout>
                      <MonitorQueue />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/learn"
                element={
                  <RequireAuth>
                    <Layout>
                      <MyCourses />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/learn/catalog"
                element={
                  <RequireAuth>
                    <Layout>
                      <Catalog />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/learn/courses/:enrollmentId"
                element={
                  <RequireAuth>
                    <Layout>
                      <Player />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route
                path="/assets/:assetId/diff"
                element={
                  <RequireAuth>
                    <Layout>
                      <Diff />
                    </Layout>
                  </RequireAuth>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
            </SessionLanguageProvider>
          </AuthProvider>
        </BrowserRouter>
    </QueryClientProvider>
  );
}
