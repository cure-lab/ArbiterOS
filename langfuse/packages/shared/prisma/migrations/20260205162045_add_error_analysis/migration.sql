-- CreateTable
CREATE TABLE "error_analyses" (
    "id" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "project_id" TEXT NOT NULL,
    "trace_id" TEXT NOT NULL,
    "observation_id" TEXT NOT NULL,
    "model" TEXT NOT NULL,
    "root_cause" TEXT NOT NULL,
    "resolve_now" TEXT[] NOT NULL,
    "prevention_next_call" TEXT[],
    "relevant_observations" TEXT[],
    "context_sufficient" BOOLEAN NOT NULL DEFAULT TRUE,
    "confidence" DOUBLE PRECISION NOT NULL,

    CONSTRAINT "error_analyses_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "error_analyses_trace_id_idx" ON "error_analyses"("trace_id");

-- CreateIndex
CREATE UNIQUE INDEX "error_analyses_project_id_observation_id_key" ON "error_analyses"("project_id", "observation_id");

-- AddForeignKey
ALTER TABLE "error_analyses" ADD CONSTRAINT "error_analyses_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
