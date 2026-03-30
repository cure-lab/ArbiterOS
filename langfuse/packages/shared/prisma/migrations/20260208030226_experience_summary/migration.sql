-- CreateTable
CREATE TABLE "experience_summaries" (
    "id" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "project_id" TEXT NOT NULL,
    "model" TEXT NOT NULL,
    "schema_version" INTEGER NOT NULL DEFAULT 1,
    "summary" JSON NOT NULL,
    "cursor_updated_at" TIMESTAMP(3),

    CONSTRAINT "experience_summaries_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "experience_summaries_project_id_key" ON "experience_summaries"("project_id");

-- CreateIndex
CREATE INDEX "experience_summaries_project_id_idx" ON "experience_summaries"("project_id");

-- AddForeignKey
ALTER TABLE "experience_summaries" ADD CONSTRAINT "experience_summaries_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
